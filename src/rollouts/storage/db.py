from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

from rollouts.errors import RolloutsError
from rollouts.models import (
    AppPaths,
    LearnRunRecord,
    LearnSessionRecord,
    RemoteDefaultsRecord,
    SnapshotRecord,
    WorkspaceRecord,
)
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

CREATE TABLE IF NOT EXISTS learn_sessions (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  session_name TEXT NOT NULL,
  dataset_repo TEXT NOT NULL,
  prime_config TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (workspace_id, session_name),
  FOREIGN KEY (workspace_id) REFERENCES workspaces(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS learn_sessions_workspace_id_idx
ON learn_sessions (workspace_id);

CREATE TABLE IF NOT EXISTS learn_runs (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  run_number INTEGER NOT NULL,
  prime_run_id TEXT,
  prime_checkpoint_id TEXT,
  prime_model_id TEXT,
  prime_config TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (session_id, run_number),
  FOREIGN KEY (session_id) REFERENCES learn_sessions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS learn_runs_session_id_run_number_idx
ON learn_runs (session_id, run_number);

CREATE UNIQUE INDEX IF NOT EXISTS learn_runs_prime_run_id_idx
ON learn_runs (prime_run_id);
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


def get_learn_session(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    session_name: str,
) -> LearnSessionRecord | None:
    row = connection.execute(
        """
        SELECT
          id,
          workspace_id,
          session_name,
          dataset_repo,
          prime_config,
          created_at,
          updated_at
        FROM learn_sessions
        WHERE workspace_id = ?
          AND session_name = ?
        """,
        (workspace_id, session_name),
    ).fetchone()
    if row is None:
        return None

    return _learn_session_from_row(row)


def save_learn_session(
    connection: sqlite3.Connection,
    *,
    workspace_id: str,
    session_name: str,
    dataset_repo: str,
    prime_config: str,
) -> LearnSessionRecord:
    existing = get_learn_session(
        connection,
        workspace_id=workspace_id,
        session_name=session_name,
    )
    session_id = existing.id if existing is not None else uuid4().hex
    created_at = existing.created_at if existing is not None else utc_now()
    updated_at = utc_now()

    connection.execute(
        """
        INSERT INTO learn_sessions (
          id,
          workspace_id,
          session_name,
          dataset_repo,
          prime_config,
          created_at,
          updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(workspace_id, session_name) DO UPDATE SET
          dataset_repo = excluded.dataset_repo,
          prime_config = excluded.prime_config,
          updated_at = excluded.updated_at
        """,
        (
            session_id,
            workspace_id,
            session_name,
            dataset_repo,
            prime_config,
            created_at.isoformat(),
            updated_at.isoformat(),
        ),
    )
    connection.commit()

    saved = get_learn_session(
        connection,
        workspace_id=workspace_id,
        session_name=session_name,
    )
    if saved is None:
        raise RuntimeError(f"learn session disappeared during save: {workspace_id}/{session_name}")
    return saved


def get_learn_run(
    connection: sqlite3.Connection,
    *,
    run_id: str,
) -> LearnRunRecord | None:
    row = connection.execute(
        """
        SELECT
          id,
          session_id,
          run_number,
          prime_run_id,
          prime_checkpoint_id,
          prime_model_id,
          prime_config,
          created_at,
          updated_at
        FROM learn_runs
        WHERE id = ?
        """,
        (run_id,),
    ).fetchone()
    if row is None:
        return None

    return _learn_run_from_row(row)


def get_learn_run_by_prime_run_id(
    connection: sqlite3.Connection,
    *,
    prime_run_id: str,
) -> LearnRunRecord | None:
    row = connection.execute(
        """
        SELECT
          id,
          session_id,
          run_number,
          prime_run_id,
          prime_checkpoint_id,
          prime_model_id,
          prime_config,
          created_at,
          updated_at
        FROM learn_runs
        WHERE prime_run_id = ?
        """,
        (prime_run_id,),
    ).fetchone()
    if row is None:
        return None

    return _learn_run_from_row(row)


def list_learn_runs(
    connection: sqlite3.Connection,
    *,
    session_id: str,
) -> list[LearnRunRecord]:
    rows = connection.execute(
        """
        SELECT
          id,
          session_id,
          run_number,
          prime_run_id,
          prime_checkpoint_id,
          prime_model_id,
          prime_config,
          created_at,
          updated_at
        FROM learn_runs
        WHERE session_id = ?
        ORDER BY run_number ASC
        """,
        (session_id,),
    ).fetchall()
    return [_learn_run_from_row(row) for row in rows]


def get_latest_learn_run(
    connection: sqlite3.Connection,
    *,
    session_id: str,
) -> LearnRunRecord | None:
    row = connection.execute(
        """
        SELECT
          id,
          session_id,
          run_number,
          prime_run_id,
          checkpoint_id,
          model_id,
          config,
          created_at,
          updated_at
        FROM learn_runs
        WHERE session_id = ?
        ORDER BY run_number DESC
        LIMIT 1
        """,
        (session_id,),
    ).fetchone()
    if row is None:
        return None

    return _learn_run_from_row(row)


def save_learn_run(
    connection: sqlite3.Connection,
    *,
    run_id: str,
    session_id: str,
    prime_run_id: str | None = None,
    prime_checkpoint_id: str | None = None,
    prime_model_id: str | None = None,
    prime_config: str,
) -> LearnRunRecord:
    existing = get_learn_run(connection, run_id=run_id)
    if existing is not None and existing.session_id != session_id:
        raise RolloutsError(
            f"run {run_id!r} is already associated with learn session {existing.session_id!r}"
        )

    run_number = (
        existing.run_number
        if existing is not None
        else _get_next_learn_run_number(connection, session_id=session_id)
    )
    created_at = existing.created_at if existing is not None else utc_now()
    updated_at = utc_now()

    connection.execute(
        """
        INSERT INTO learn_runs (
          id,
          session_id,
          run_number,
          prime_run_id,
          prime_checkpoint_id,
          prime_model_id,
          prime_config,
          created_at,
          updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
          prime_run_id = excluded.prime_run_id,
          prime_checkpoint_id = excluded.prime_checkpoint_id,
          prime_model_id = excluded.prime_model_id,
          prime_config = excluded.prime_config,
          updated_at = excluded.updated_at
        """,
        (
            run_id,
            session_id,
            run_number,
            prime_run_id,
            prime_checkpoint_id,
            prime_model_id,
            prime_config,
            created_at.isoformat(),
            updated_at.isoformat(),
        ),
    )
    connection.commit()

    saved = get_learn_run(connection, run_id=run_id)
    if saved is None:
        raise RuntimeError(f"learn run disappeared during save: {run_id}")
    return saved


def _get_next_learn_run_number(
    connection: sqlite3.Connection,
    *,
    session_id: str,
) -> int:
    row = connection.execute(
        """
        SELECT COALESCE(MAX(run_number), 0) AS max_run_number
        FROM learn_runs
        WHERE session_id = ?
        """,
        (session_id,),
    ).fetchone()
    return int(row["max_run_number"]) + 1


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


def list_session_ids(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        """
        SELECT DISTINCT session_id
        FROM snapshots
        ORDER BY session_id ASC
        """
    ).fetchall()
    return [row["session_id"] for row in rows]


@dataclass(frozen=True)
class SessionSummary:
    session_id: str
    workspace_id: str
    workspace_root_path: Path
    snapshot_count: int
    first_captured_at: datetime
    last_captured_at: datetime


def list_sessions(connection: sqlite3.Connection) -> list[SessionSummary]:
    rows = connection.execute(
        """
        SELECT
            s.session_id,
            s.workspace_id,
            w.root_path AS workspace_root_path,
            COUNT(*) AS snapshot_count,
            MIN(s.captured_at) AS first_captured_at,
            MAX(s.captured_at) AS last_captured_at
        FROM snapshots s
        INNER JOIN workspaces w ON w.id = s.workspace_id
        GROUP BY s.session_id, s.workspace_id
        ORDER BY MAX(s.captured_at) DESC
        """
    ).fetchall()
    return [
        SessionSummary(
            session_id=row["session_id"],
            workspace_id=row["workspace_id"],
            workspace_root_path=Path(row["workspace_root_path"]),
            snapshot_count=row["snapshot_count"],
            first_captured_at=datetime.fromisoformat(row["first_captured_at"]),
            last_captured_at=datetime.fromisoformat(row["last_captured_at"]),
        )
        for row in rows
    ]


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


def _learn_session_from_row(row: sqlite3.Row) -> LearnSessionRecord:
    return LearnSessionRecord(
        id=row["id"],
        workspace_id=row["workspace_id"],
        session_name=row["session_name"],
        dataset_repo=row["dataset_repo"],
        prime_config=row["prime_config"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _learn_run_from_row(row: sqlite3.Row) -> LearnRunRecord:
    return LearnRunRecord(
        id=row["id"],
        session_id=row["session_id"],
        run_number=row["run_number"],
        prime_run_id=row["prime_run_id"],
        prime_checkpoint_id=row["prime_checkpoint_id"],
        prime_model_id=row["prime_model_id"],
        prime_config=row["prime_config"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
