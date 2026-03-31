from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from rollouts.errors import RolloutsError
from rollouts.models import AppPaths, RemoteDefaultsRecord, SnapshotRecord, WorkspaceRecord
from rollouts.utils import utc_now


class RolloutsConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_val, exc_tb) -> Literal[False]:
        try:
            return super().__exit__(exc_type, exc_val, exc_tb)
        finally:
            self.close()


SCHEMA = """
CREATE TABLE IF NOT EXISTS workspaces (
  id TEXT PRIMARY KEY,
  root_path TEXT NOT NULL UNIQUE,
  store_path TEXT NOT NULL,
  remote_url TEXT UNIQUE,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS snapshots (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  message_id TEXT NOT NULL,
  store_commit_sha TEXT NOT NULL,
  vcs TEXT NOT NULL,
  metadata TEXT NOT NULL,
  captured_at TEXT NOT NULL,
  UNIQUE (workspace_id, session_id, message_id),
  FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
);

CREATE INDEX IF NOT EXISTS snapshots_session_id_idx
ON snapshots (session_id);

CREATE TRIGGER IF NOT EXISTS snapshots_session_workspace_guard
BEFORE INSERT ON snapshots
FOR EACH ROW
WHEN EXISTS (
  SELECT 1
  FROM snapshots
  WHERE session_id = NEW.session_id
    AND workspace_id != NEW.workspace_id
)
BEGIN
  SELECT RAISE(ABORT, 'session is already associated with a different workspace');
END;

CREATE TABLE IF NOT EXISTS remote_defaults (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  owner TEXT NOT NULL,
  repo_prefix TEXT NOT NULL,
  visibility TEXT NOT NULL CHECK (visibility IN ('private', 'public', 'internal'))
);
"""


def connect(paths: AppPaths) -> sqlite3.Connection:
    connection = sqlite3.connect(paths.db_path, factory=RolloutsConnection)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON;")
    return connection


def initialize_db(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    connection.commit()


def get_workspace_by_root_path(
    connection: sqlite3.Connection, root_path: Path
) -> WorkspaceRecord | None:
    row = connection.execute(
        """
        SELECT id, root_path, store_path, remote_url, created_at
        FROM workspaces
        WHERE root_path = ?
        """,
        (str(root_path),),
    ).fetchone()

    if row is None:
        return None

    return _workspace_from_row(row)


def _get_workspace_for_path(
    connection: sqlite3.Connection, workspace_path: Path
) -> WorkspaceRecord | None:
    resolved_path = workspace_path.resolve(strict=False)
    rows = connection.execute(
        """
        SELECT id, root_path, store_path, remote_url, created_at
        FROM workspaces
        """
    ).fetchall()

    matches = [
        record
        for row in rows
        if resolved_path == (record := _workspace_from_row(row)).root_path
        or record.root_path in resolved_path.parents
    ]
    if not matches:
        return None

    return max(matches, key=lambda record: len(record.root_path.parts))


def find_workspace(
    connection: sqlite3.Connection,
    *,
    workspace_path: Path,
    resolved_root_path: Path,
) -> WorkspaceRecord | None:
    exact_workspace = get_workspace_by_root_path(connection, resolved_root_path)
    if exact_workspace is not None:
        return exact_workspace

    return _get_workspace_for_path(connection, workspace_path)


def create_workspace(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    root_path: Path,
    store_path: Path,
) -> WorkspaceRecord:
    created_at = utc_now()
    connection.execute(
        """
        INSERT INTO workspaces (id, root_path, store_path, remote_url, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (workspace_id, str(root_path), str(store_path), None, created_at.isoformat()),
    )
    connection.commit()

    return WorkspaceRecord(
        id=workspace_id,
        root_path=root_path,
        store_path=store_path,
        remote_url=None,
        created_at=created_at,
    )


def update_workspace_root_path(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    root_path: Path,
) -> WorkspaceRecord:
    connection.execute(
        """
        UPDATE workspaces
        SET root_path = ?
        WHERE id = ?
        """,
        (str(root_path), workspace_id),
    )
    connection.commit()
    return _require_workspace_by_id(
        connection,
        workspace_id=workspace_id,
        missing_message=f"workspace disappeared during root update: {workspace_id}",
    )


def set_workspace_remote_url(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    remote_url: str,
) -> WorkspaceRecord:
    conflicting_row = connection.execute(
        """
        SELECT id, root_path, store_path, remote_url, created_at
        FROM workspaces
        WHERE remote_url = ? AND id != ?
        """,
        (remote_url, workspace_id),
    ).fetchone()
    if conflicting_row is not None:
        conflicting_workspace = _workspace_from_row(conflicting_row)
        raise RolloutsError(
            f"remote URL is already configured for workspace: {conflicting_workspace.root_path}"
        )

    connection.execute(
        """
        UPDATE workspaces
        SET remote_url = ?
        WHERE id = ?
        """,
        (remote_url, workspace_id),
    )
    try:
        connection.commit()
    except sqlite3.IntegrityError as error:
        raise RolloutsError(f"remote URL is already configured: {remote_url}") from error

    return _require_workspace_by_id(
        connection,
        workspace_id=workspace_id,
        missing_message=f"workspace disappeared during remote update: {workspace_id}",
    )


def clear_workspace_remote_url(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
) -> WorkspaceRecord:
    connection.execute(
        """
        UPDATE workspaces
        SET remote_url = NULL
        WHERE id = ?
        """,
        (workspace_id,),
    )
    connection.commit()
    return _require_workspace_by_id(
        connection,
        workspace_id=workspace_id,
        missing_message=f"workspace disappeared during remote clear: {workspace_id}",
    )


def clear_all_workspace_remote_urls(connection: sqlite3.Connection) -> int:
    cursor = connection.execute(
        """
        UPDATE workspaces
        SET remote_url = NULL
        WHERE remote_url IS NOT NULL
        """
    )
    connection.commit()
    return cursor.rowcount


def get_remote_defaults(connection: sqlite3.Connection) -> RemoteDefaultsRecord | None:
    row = connection.execute(
        """
        SELECT owner, repo_prefix, visibility
        FROM remote_defaults
        WHERE id = 1
        """
    ).fetchone()
    if row is None:
        return None

    return RemoteDefaultsRecord(
        owner=row["owner"],
        repo_prefix=row["repo_prefix"],
        visibility=row["visibility"],
    )


def set_remote_defaults(
    connection: sqlite3.Connection,
    *,
    owner: str,
    repo_prefix: str,
    visibility: str,
) -> RemoteDefaultsRecord:
    connection.execute(
        """
        INSERT INTO remote_defaults (id, owner, repo_prefix, visibility)
        VALUES (1, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          owner = excluded.owner,
          repo_prefix = excluded.repo_prefix,
          visibility = excluded.visibility
        """,
        (owner, repo_prefix, visibility),
    )
    connection.commit()

    return RemoteDefaultsRecord(
        owner=owner,
        repo_prefix=repo_prefix,
        visibility=visibility,
    )


def create_snapshot(
    connection: sqlite3.Connection,
    *,
    snapshot_id: str,
    workspace_id: str,
    session_id: str,
    message_id: str,
    store_commit_sha: str,
    vcs: str,
    metadata: str,
) -> SnapshotRecord:
    existing_workspace = get_workspace_for_session(connection, session_id=session_id)
    if existing_workspace is not None and existing_workspace.id != workspace_id:
        current_workspace = _get_workspace_by_id(connection, workspace_id=workspace_id)
        if current_workspace is None:
            raise RuntimeError(f"workspace disappeared during snapshot insert: {workspace_id}")
        raise RolloutsError(
            f"session {session_id!r} is already associated with workspace "
            f"{existing_workspace.root_path} and cannot be snapshotted under "
            f"{current_workspace.root_path}"
        )

    captured_at = utc_now()
    try:
        connection.execute(
            """
            INSERT INTO snapshots (
              id,
              workspace_id,
              session_id,
              message_id,
              store_commit_sha,
              vcs,
              metadata,
              captured_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                workspace_id,
                session_id,
                message_id,
                store_commit_sha,
                vcs,
                metadata,
                captured_at.isoformat(),
            ),
        )
    except sqlite3.IntegrityError as error:
        if "session is already associated with a different workspace" in str(error):
            raise RolloutsError(
                f"session {session_id!r} is already associated with a different workspace"
            ) from error
        raise RolloutsError(
            f"snapshot already exists for session {session_id!r} and message {message_id!r}"
        ) from error
    connection.commit()

    return SnapshotRecord(
        id=snapshot_id,
        workspace_id=workspace_id,
        session_id=session_id,
        message_id=message_id,
        store_commit_sha=store_commit_sha,
        vcs=vcs,
        metadata=metadata,
        captured_at=captured_at,
    )


def get_snapshot_by_message(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    session_id: str,
    message_id: str,
) -> SnapshotRecord | None:
    row = connection.execute(
        """
        SELECT
          id,
          workspace_id,
          session_id,
          message_id,
          store_commit_sha,
          vcs,
          metadata,
          captured_at
        FROM snapshots
        WHERE workspace_id = ?
          AND session_id = ?
          AND message_id = ?
        """,
        (workspace_id, session_id, message_id),
    ).fetchone()

    if row is None:
        return None

    return _snapshot_from_row(row)


def list_snapshots(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    session_id: str | None = None,
    message_id: str | None = None,
) -> list[SnapshotRecord]:
    query = """
        SELECT
          id,
          workspace_id,
          session_id,
          message_id,
          store_commit_sha,
          vcs,
          metadata,
          captured_at
        FROM snapshots
        WHERE workspace_id = ?
    """
    params: list[str] = [workspace_id]
    if session_id is not None:
        query += "\n  AND session_id = ?"
        params.append(session_id)
    if message_id is not None:
        query += "\n  AND message_id = ?"
        params.append(message_id)
    query += "\n  ORDER BY captured_at ASC"

    rows = connection.execute(query, params).fetchall()
    return [_snapshot_from_row(row) for row in rows]


def get_workspace_for_session(
    connection: sqlite3.Connection,
    *,
    session_id: str,
) -> WorkspaceRecord | None:
    rows = connection.execute(
        """
        SELECT DISTINCT w.id, w.root_path, w.store_path, w.remote_url, w.created_at
        FROM snapshots s
        INNER JOIN workspaces w ON w.id = s.workspace_id
        WHERE s.session_id = ?
        ORDER BY w.root_path ASC
        """,
        (session_id,),
    ).fetchall()

    if not rows:
        return None

    workspaces = [_workspace_from_row(row) for row in rows]
    if len(workspaces) > 1:
        roots = ", ".join(str(workspace.root_path) for workspace in workspaces)
        raise RolloutsError(
            f"session {session_id!r} is associated with multiple workspaces: {roots}"
        )

    return workspaces[0]


def list_workspaces(
    connection: sqlite3.Connection,
    *,
    only_with_remote: bool = False,
) -> list[WorkspaceRecord]:
    query = """
        SELECT id, root_path, store_path, remote_url, created_at
        FROM workspaces
    """
    if only_with_remote:
        query += "\nWHERE remote_url IS NOT NULL"
    query += "\nORDER BY root_path ASC"

    rows = connection.execute(query).fetchall()
    return [_workspace_from_row(row) for row in rows]


def delete_snapshots(connection: sqlite3.Connection, *, snapshot_ids: Sequence[str]) -> int:
    if not snapshot_ids:
        return 0

    placeholders = ", ".join("?" for _ in snapshot_ids)
    cursor = connection.execute(
        f"DELETE FROM snapshots WHERE id IN ({placeholders})",
        list(snapshot_ids),
    )
    connection.commit()
    return cursor.rowcount


def delete_workspace(connection: sqlite3.Connection, *, workspace_id: str) -> int:
    cursor = connection.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
    connection.commit()
    return cursor.rowcount


def _require_workspace_by_id(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    missing_message: str,
) -> WorkspaceRecord:
    row = connection.execute(
        """
        SELECT id, root_path, store_path, remote_url, created_at
        FROM workspaces
        WHERE id = ?
        """,
        (workspace_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError(missing_message)

    return _workspace_from_row(row)


def _get_workspace_by_id(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
) -> WorkspaceRecord | None:
    row = connection.execute(
        """
        SELECT id, root_path, store_path, remote_url, created_at
        FROM workspaces
        WHERE id = ?
        """,
        (workspace_id,),
    ).fetchone()

    if row is None:
        return None

    return _workspace_from_row(row)


def _workspace_from_row(row: sqlite3.Row) -> WorkspaceRecord:
    return WorkspaceRecord(
        id=row["id"],
        root_path=Path(row["root_path"]),
        store_path=Path(row["store_path"]),
        remote_url=row["remote_url"],
        created_at=row["created_at"],
    )


def _snapshot_from_row(row: sqlite3.Row) -> SnapshotRecord:
    return SnapshotRecord(
        id=row["id"],
        workspace_id=row["workspace_id"],
        session_id=row["session_id"],
        message_id=row["message_id"],
        store_commit_sha=row["store_commit_sha"],
        vcs=row["vcs"],
        metadata=row["metadata"],
        captured_at=row["captured_at"],
    )
