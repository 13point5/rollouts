from __future__ import annotations

import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from rollouts.errors import RolloutsError
from rollouts.models import SnapshotRecord
from rollouts.paths import get_app_paths
from rollouts.storage.db import (
    connect,
    delete_snapshots,
    delete_workspace,
    initialize_db,
    list_snapshots,
)
from rollouts.storage.git_store import delete_snapshot_ref
from rollouts.storage.workspace import get_existing_workspace


@dataclass(frozen=True)
class DeleteResult:
    deleted_snapshots: int
    deleted_workspace: bool
    deleted_all: bool
    deleted_path: Path | None = None


def delete_data(
    *,
    workspace: Path,
    session_id: str | None,
    message_id: str | None,
    delete_all: bool,
) -> DeleteResult:
    validate_delete_args(
        session_id=session_id,
        message_id=message_id,
        delete_all=delete_all,
    )

    paths = get_app_paths()
    if delete_all:
        return _delete_all_data(paths.home)

    if not paths.db_path.exists():
        raise RolloutsError("no rollouts data found")

    workspace_record = get_existing_workspace(workspace, ensure_home=False)
    workspace_path = workspace.resolve(strict=False)

    with connect(paths) as connection:
        initialize_db(connection)
        snapshots = _get_target_snapshots(
            connection=connection,
            workspace_id=workspace_record.id,
            session_id=session_id,
            message_id=message_id,
        )

        for snapshot in snapshots:
            delete_snapshot_ref(store_path=workspace_record.store_path, snapshot_id=snapshot.id)

        deleted_snapshot_count = delete_snapshots(
            connection,
            snapshot_ids=[snapshot.id for snapshot in snapshots],
        )

        deleted_workspace = False
        if session_id is None:
            delete_workspace(connection, workspace_id=workspace_record.id)
            shutil.rmtree(workspace_record.store_path.parent, ignore_errors=True)
            deleted_workspace = True

    return DeleteResult(
        deleted_snapshots=deleted_snapshot_count,
        deleted_workspace=deleted_workspace,
        deleted_all=False,
        deleted_path=workspace_path,
    )


def _get_target_snapshots(
    *,
    connection: sqlite3.Connection,
    workspace_id: str,
    session_id: str | None,
    message_id: str | None,
) -> list[SnapshotRecord]:
    snapshots = list_snapshots(
        connection,
        workspace_id=workspace_id,
        session_id=session_id,
        message_id=message_id,
    )
    if session_id is None:
        return snapshots

    if snapshots:
        return snapshots

    if message_id is not None:
        raise RolloutsError(
            f"no snapshot found for session {session_id!r} and message {message_id!r}"
        )
    raise RolloutsError(f"no snapshots found for session {session_id!r}")


def _delete_all_data(home_path: Path) -> DeleteResult:
    if home_path.exists():
        shutil.rmtree(home_path, ignore_errors=True)

    return DeleteResult(
        deleted_snapshots=0,
        deleted_workspace=False,
        deleted_all=True,
        deleted_path=home_path,
    )


def validate_delete_args(
    *,
    session_id: str | None,
    message_id: str | None,
    delete_all: bool,
) -> None:
    if message_id is not None and session_id is None:
        raise RolloutsError("--message requires --session")
    if delete_all and (session_id is not None or message_id is not None):
        raise RolloutsError("--all cannot be combined with --session or --message")
