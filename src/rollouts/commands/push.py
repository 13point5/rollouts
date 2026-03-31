from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from rollouts.errors import RolloutsError
from rollouts.github import create_github_archive_repo
from rollouts.models import SnapshotRecord, WorkspaceRecord
from rollouts.paths import ensure_app_home, get_app_paths
from rollouts.storage.db import (
    connect,
    get_remote_defaults,
    initialize_db,
    list_snapshots,
    list_workspaces,
    set_workspace_remote_url,
)
from rollouts.storage.git_store import push_snapshot_tag
from rollouts.storage.workspace import get_existing_workspace


@dataclass(frozen=True)
class PushResult:
    pushed_snapshots: int
    skipped_snapshots: int
    workspace_count: int
    created_remotes: int


@dataclass(frozen=True)
class PushScopeCounts:
    workspace_count: int
    snapshot_count: int


@dataclass(frozen=True)
class PushWorkspaceBatch:
    workspace_record: WorkspaceRecord
    snapshots: list[SnapshotRecord]


WorkspaceProgressCallback = Callable[[WorkspaceRecord], None]
SnapshotProgressCallback = Callable[[SnapshotRecord], None]


def push_snapshots(
    *,
    workspace: Path,
    session_id: str | None,
    message_id: str | None,
    push_all: bool,
    create_remote: bool,
    on_workspace_pushed: WorkspaceProgressCallback | None = None,
    on_snapshot_pushed: SnapshotProgressCallback | None = None,
) -> PushResult:
    validate_push_args(
        session_id=session_id,
        message_id=message_id,
        push_all=push_all,
        create_remote=create_remote,
    )

    paths = get_app_paths()
    ensure_app_home(paths)

    with connect(paths) as connection:
        initialize_db(connection)

        workspace_batches = _resolve_push_scope(
            connection=connection,
            workspace=workspace,
            session_id=session_id,
            message_id=message_id,
            push_all=push_all,
            create_remote=create_remote,
        )

        return _push_workspace_batches(
            connection=connection,
            workspace_batches=workspace_batches,
            create_remote=create_remote,
            on_workspace_pushed=on_workspace_pushed,
            on_snapshot_pushed=on_snapshot_pushed,
        )


def get_push_scope_counts(
    *,
    workspace: Path,
    session_id: str | None,
    message_id: str | None,
    push_all: bool,
    create_remote: bool,
) -> PushScopeCounts:
    validate_push_args(
        session_id=session_id,
        message_id=message_id,
        push_all=push_all,
        create_remote=create_remote,
    )

    paths = get_app_paths()
    ensure_app_home(paths)
    with connect(paths) as connection:
        initialize_db(connection)
        workspace_batches = _resolve_push_scope(
            connection=connection,
            workspace=workspace,
            session_id=session_id,
            message_id=message_id,
            push_all=push_all,
            create_remote=create_remote,
        )
        return PushScopeCounts(
            workspace_count=len(workspace_batches),
            snapshot_count=sum(len(batch.snapshots) for batch in workspace_batches),
        )


def validate_push_args(
    *,
    session_id: str | None,
    message_id: str | None,
    push_all: bool,
    create_remote: bool,
) -> None:
    del create_remote
    if message_id is not None and session_id is None:
        raise RolloutsError("--message requires --session")
    if push_all and (session_id is not None or message_id is not None):
        raise RolloutsError("--all cannot be combined with --session or --message")


def _resolve_push_scope(
    *,
    connection: sqlite3.Connection,
    workspace: Path,
    session_id: str | None,
    message_id: str | None,
    push_all: bool,
    create_remote: bool,
) -> list[PushWorkspaceBatch]:
    if push_all:
        workspaces = list_workspaces(connection, only_with_remote=not create_remote)
        if not workspaces:
            if create_remote:
                raise RolloutsError("no registered workspaces found")
            raise RolloutsError("no workspaces with configured remotes")

        workspace_batches = [
            PushWorkspaceBatch(
                workspace_record=workspace_record,
                snapshots=snapshots,
            )
            for workspace_record in workspaces
            if (snapshots := list_snapshots(connection, workspace_id=workspace_record.id))
        ]
        if not workspace_batches:
            if create_remote:
                raise RolloutsError("no snapshots found for registered workspaces")
            raise RolloutsError("no snapshots found for workspaces with configured remotes")

        return workspace_batches

    workspace_record = get_existing_workspace(workspace)
    snapshots = list_snapshots(
        connection,
        workspace_id=workspace_record.id,
        session_id=session_id,
        message_id=message_id,
    )
    if not snapshots:
        raise _missing_snapshot_error(
            workspace=workspace_record.root_path,
            session_id=session_id,
            message_id=message_id,
        )

    return [
        PushWorkspaceBatch(
            workspace_record=workspace_record,
            snapshots=snapshots,
        )
    ]


def _push_workspace_batches(
    *,
    connection: sqlite3.Connection,
    workspace_batches: list[PushWorkspaceBatch],
    create_remote: bool,
    on_workspace_pushed: WorkspaceProgressCallback | None,
    on_snapshot_pushed: SnapshotProgressCallback | None,
) -> PushResult:
    pushed_total = 0
    skipped_total = 0
    workspace_count = 0
    created_remotes = 0

    for workspace_batch in workspace_batches:
        workspace_record = workspace_batch.workspace_record
        workspace_record, created_remote = _ensure_workspace_remote(
            connection=connection,
            workspace_record=workspace_record,
            create_remote=create_remote,
        )
        remote_url = workspace_record.remote_url
        if remote_url is None:
            raise RuntimeError("workspace remote disappeared during batch push")

        pushed_count, skipped_count = _push_snapshot_batch(
            store_path=workspace_record.store_path,
            remote_url=remote_url,
            snapshots=workspace_batch.snapshots,
            on_snapshot_pushed=on_snapshot_pushed,
        )

        pushed_total += pushed_count
        skipped_total += skipped_count
        workspace_count += 1
        created_remotes += 1 if created_remote else 0
        if on_workspace_pushed is not None:
            on_workspace_pushed(workspace_record)

    return PushResult(
        pushed_snapshots=pushed_total,
        skipped_snapshots=skipped_total,
        workspace_count=workspace_count,
        created_remotes=created_remotes,
    )


def _push_snapshot_batch(
    *,
    store_path: Path,
    remote_url: str,
    snapshots: list[SnapshotRecord],
    on_snapshot_pushed: SnapshotProgressCallback | None,
) -> tuple[int, int]:
    pushed_count = 0
    skipped_count = 0

    for snapshot in snapshots:
        result = push_snapshot_tag(
            store_path=store_path,
            remote_url=remote_url,
            snapshot=snapshot,
        )
        if result.pushed:
            pushed_count += 1
        else:
            skipped_count += 1
        if on_snapshot_pushed is not None:
            on_snapshot_pushed(snapshot)

    return pushed_count, skipped_count


def _missing_snapshot_error(
    *,
    workspace: Path,
    session_id: str | None,
    message_id: str | None,
) -> RolloutsError:
    if session_id is None:
        return RolloutsError(f"no snapshots found for workspace {workspace}")
    if message_id is None:
        return RolloutsError(f"no snapshots found for session {session_id!r}")
    return RolloutsError(f"no snapshot found for session {session_id!r} and message {message_id!r}")


def _ensure_workspace_remote(
    *,
    connection: sqlite3.Connection,
    workspace_record: WorkspaceRecord,
    create_remote: bool,
) -> tuple[WorkspaceRecord, bool]:
    if workspace_record.remote_url is not None:
        return workspace_record, False
    if not create_remote:
        raise RolloutsError(f"workspace has no configured remote: {workspace_record.root_path}")

    defaults = get_remote_defaults(connection)
    if defaults is None:
        raise RolloutsError(
            "no remote defaults configured; run `rollouts remote defaults set` first"
        )

    remote_url = create_github_archive_repo(
        workspace=workspace_record,
        defaults=defaults,
    )
    updated_workspace = set_workspace_remote_url(
        connection,
        workspace_id=workspace_record.id,
        remote_url=remote_url,
    )
    return updated_workspace, True
