from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from rollouts.db import connect, create_snapshot, initialize_db
from rollouts.errors import RolloutsError
from rollouts.git_store import (
    create_snapshot_commit,
    delete_snapshot_ref,
    resolve_workspace_source,
)
from rollouts.models import SnapshotRecord
from rollouts.paths import ensure_app_home, get_app_paths
from rollouts.workspace import ensure_workspace


def snapshot_workspace(
    *,
    workspace: Path,
    session_id: str,
    message_id: str,
    metadata: str,
) -> SnapshotRecord:
    metadata = _validate_metadata(metadata)
    resolved_workspace = resolve_workspace_source(workspace)
    workspace_result = ensure_workspace(workspace)
    workspace_record = workspace_result.workspace

    paths = get_app_paths()
    ensure_app_home(paths)
    with connect(paths) as connection:
        initialize_db(connection)
        snapshot_id = uuid4().hex

        try:
            store_commit_sha = create_snapshot_commit(
                workspace_root=workspace_record.root_path,
                is_git=resolved_workspace.is_git,
                store_path=workspace_record.store_path,
                snapshot_id=snapshot_id,
                session_id=session_id,
                message_id=message_id,
                excluded_paths=(paths.home,),
            )
            return _insert_snapshot(
                connection=connection,
                snapshot_id=snapshot_id,
                workspace_id=workspace_record.id,
                session_id=session_id,
                message_id=message_id,
                store_commit_sha=store_commit_sha,
                vcs=resolved_workspace.vcs,
                metadata=metadata,
            )
        except Exception:
            delete_snapshot_ref(store_path=workspace_record.store_path, snapshot_id=snapshot_id)
            raise


def _validate_metadata(raw_metadata: str) -> str:
    try:
        json.loads(raw_metadata)
    except json.JSONDecodeError as error:
        raise RolloutsError(f"invalid metadata JSON: {error.msg}") from error

    return raw_metadata


def _insert_snapshot(
    *,
    connection: sqlite3.Connection,
    snapshot_id: str,
    workspace_id: str,
    session_id: str,
    message_id: str,
    store_commit_sha: str,
    vcs: str,
    metadata: str,
) -> SnapshotRecord:
    snapshot = create_snapshot(
        connection,
        snapshot_id=snapshot_id,
        workspace_id=workspace_id,
        session_id=session_id,
        message_id=message_id,
        store_commit_sha=store_commit_sha,
        vcs=vcs,
        metadata=metadata,
    )

    return snapshot
