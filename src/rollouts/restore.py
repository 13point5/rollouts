from __future__ import annotations

from pathlib import Path

from rollouts.db import connect, get_latest_snapshot, get_workspace_by_root_path, initialize_db
from rollouts.errors import RolloutsError
from rollouts.git_store import resolve_git_workspace_root, restore_snapshot_to_destination
from rollouts.models import SnapshotRecord
from rollouts.paths import ensure_app_home, get_app_paths


def restore_workspace(
    *,
    workspace: Path,
    session_id: str,
    turn_id: str,
    destination: Path,
) -> SnapshotRecord:
    paths = get_app_paths()
    ensure_app_home(paths)
    workspace_root = resolve_git_workspace_root(workspace)

    with connect(paths) as connection:
        initialize_db(connection)
        workspace_record = get_workspace_by_root_path(connection, workspace_root)
        if workspace_record is None:
            raise RolloutsError(f"workspace is not initialized: {workspace_root}")

        snapshot = get_latest_snapshot(
            connection,
            workspace_id=workspace_record.id,
            session_id=session_id,
            turn_id=turn_id,
        )
        if snapshot is None:
            raise RolloutsError(
                f"no snapshot found for session {session_id!r} and turn {turn_id!r}"
            )

        restore_snapshot_to_destination(
            store_path=workspace_record.store_path,
            destination=destination,
            store_commit_sha=snapshot.store_commit_sha,
        )
        return snapshot
