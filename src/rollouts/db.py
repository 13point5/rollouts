from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from rollouts.models import AppPaths, WorkspaceRecord

SCHEMA = """
CREATE TABLE IF NOT EXISTS workspaces (
  id TEXT PRIMARY KEY,
  root_path TEXT NOT NULL UNIQUE,
  store_path TEXT NOT NULL,
  created_at TEXT NOT NULL
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


def _workspace_from_row(row: sqlite3.Row) -> WorkspaceRecord:
    return WorkspaceRecord(
        id=row["id"],
        root_path=Path(row["root_path"]),
        store_path=Path(row["store_path"]),
        created_at=row["created_at"],
    )
