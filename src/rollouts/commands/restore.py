from __future__ import annotations

from pathlib import Path

from rollouts.errors import RolloutsError
from rollouts.github import normalize_github_repo_clone_url
from rollouts.models import SnapshotRecord
from rollouts.paths import ensure_app_home, get_app_paths
from rollouts.storage.db import connect, get_snapshot_by_message, initialize_db
from rollouts.storage.git_store import (
    RemoteRestoreResult,
    restore_remote_snapshot_to_destination,
    restore_snapshot_to_destination,
)
from rollouts.storage.workspace import get_existing_workspace


def restore_workspace(
    *,
    workspace: Path,
    session_id: str,
    message_id: str,
    destination: Path,
) -> SnapshotRecord:
    paths = get_app_paths()
    ensure_app_home(paths)
    workspace_record = get_existing_workspace(workspace)

    with connect(paths) as connection:
        initialize_db(connection)
        snapshot = get_snapshot_by_message(
            connection,
            workspace_id=workspace_record.id,
            session_id=session_id,
            message_id=message_id,
        )
        if snapshot is None:
            raise RolloutsError(
                f"no snapshot found for session {session_id!r} and message {message_id!r}"
            )

        restore_snapshot_to_destination(
            store_path=workspace_record.store_path,
            destination=destination,
            store_commit_sha=snapshot.store_commit_sha,
        )
        return snapshot


def restore_remote_workspace(
    *,
    repo_url: str,
    session_id: str,
    message_id: str,
    destination: Path,
) -> RemoteRestoreResult:
    normalized_repo_url = normalize_github_repo_clone_url(repo_url)
    if not normalized_repo_url:
        raise RolloutsError("repo URL cannot be empty")

    return restore_remote_snapshot_to_destination(
        repo_url=normalized_repo_url,
        session_id=session_id,
        message_id=message_id,
        destination=destination,
    )
