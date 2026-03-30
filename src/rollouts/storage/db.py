from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from rollouts.errors import RolloutsError
from rollouts.models import AppPaths, SnapshotRecord, WorkspaceRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS workspaces (
  id TEXT PRIMARY KEY,
  root_path TEXT NOT NULL UNIQUE,
  store_path TEXT NOT NULL,
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
"""


def connect(paths: AppPaths) -> sqlite3.Connection:
    connection = sqlite3.connect(paths.db_path)
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
        SELECT id, root_path, store_path, created_at
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
        SELECT id, root_path, store_path, created_at
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
    created_at = datetime.now(timezone.utc)
    connection.execute(
        """
        INSERT INTO workspaces (id, root_path, store_path, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (workspace_id, str(root_path), str(store_path), created_at.isoformat()),
    )
    connection.commit()

    return WorkspaceRecord(
        id=workspace_id,
        root_path=root_path,
        store_path=store_path,
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

    row = connection.execute(
        """
        SELECT id, root_path, store_path, created_at
        FROM workspaces
        WHERE id = ?
        """,
        (workspace_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"workspace disappeared during root update: {workspace_id}")

    return _workspace_from_row(row)


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
    captured_at = datetime.now(timezone.utc)
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

    rows = connection.execute(query, params).fetchall()
    return [_snapshot_from_row(row) for row in rows]


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


def _workspace_from_row(row: sqlite3.Row) -> WorkspaceRecord:
    return WorkspaceRecord(
        id=row["id"],
        root_path=Path(row["root_path"]),
        store_path=Path(row["store_path"]),
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
