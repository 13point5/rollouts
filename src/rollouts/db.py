from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

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
  turn_id TEXT NOT NULL,
  store_commit_sha TEXT NOT NULL,
  metadata TEXT NOT NULL,
  captured_at TEXT NOT NULL,
  FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
);

CREATE INDEX IF NOT EXISTS snapshots_lookup_idx
  ON snapshots (workspace_id, session_id, turn_id, captured_at);
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


def create_snapshot(
    connection: sqlite3.Connection,
    *,
    snapshot_id: str,
    workspace_id: str,
    session_id: str,
    turn_id: str,
    store_commit_sha: str,
    metadata: str,
) -> SnapshotRecord:
    captured_at = datetime.now(timezone.utc)
    connection.execute(
        """
        INSERT INTO snapshots (
          id,
          workspace_id,
          session_id,
          turn_id,
          store_commit_sha,
          metadata,
          captured_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot_id,
            workspace_id,
            session_id,
            turn_id,
            store_commit_sha,
            metadata,
            captured_at.isoformat(),
        ),
    )
    connection.commit()

    return SnapshotRecord(
        id=snapshot_id,
        workspace_id=workspace_id,
        session_id=session_id,
        turn_id=turn_id,
        store_commit_sha=store_commit_sha,
        metadata=metadata,
        captured_at=captured_at,
    )


def _workspace_from_row(row: sqlite3.Row) -> WorkspaceRecord:
    return WorkspaceRecord(
        id=row["id"],
        root_path=Path(row["root_path"]),
        store_path=Path(row["store_path"]),
        created_at=row["created_at"],
    )
