from __future__ import annotations

from pathlib import Path

from rollouts.errors import RolloutsError
from rollouts.github import ensure_github_cli_available
from rollouts.models import RemoteDefaultsRecord, WorkspaceRecord
from rollouts.paths import ensure_app_home, get_app_paths
from rollouts.storage.db import (
    clear_all_workspace_remote_urls,
    clear_workspace_remote_url,
    connect,
    find_workspace,
    initialize_db,
    set_remote_defaults,
    set_workspace_remote_url,
)
from rollouts.storage.git_store import resolve_workspace_source
from rollouts.storage.workspace import ensure_workspace


def set_workspace_remote(*, workspace: Path, remote_url: str) -> WorkspaceRecord:
    remote_url = remote_url.strip()
    if not remote_url:
        raise RolloutsError("remote URL cannot be empty")

    workspace_record = ensure_workspace(workspace)

    paths = get_app_paths()
    ensure_app_home(paths)
    with connect(paths) as connection:
        initialize_db(connection)
        return set_workspace_remote_url(
            connection,
            workspace_id=workspace_record.id,
            remote_url=remote_url,
        )


def set_global_remote_defaults(
    *,
    owner: str,
    repo_prefix: str,
    visibility: str,
) -> RemoteDefaultsRecord:
    owner = owner.strip()
    repo_prefix = repo_prefix.strip()
    if not owner:
        raise RolloutsError("owner cannot be empty")
    if not repo_prefix:
        raise RolloutsError("prefix cannot be empty")
    if visibility not in {"private", "public", "internal"}:
        raise RolloutsError("visibility must be one of: private, public, internal")

    ensure_github_cli_available()

    paths = get_app_paths()
    ensure_app_home(paths)
    with connect(paths) as connection:
        initialize_db(connection)
        return set_remote_defaults(
            connection,
            owner=owner,
            repo_prefix=repo_prefix,
            visibility=visibility,
        )


def clear_workspace_remote(*, workspace: Path) -> WorkspaceRecord:
    paths = get_app_paths()
    ensure_app_home(paths)
    workspace_path = workspace.resolve(strict=False)
    resolved_workspace = resolve_workspace_source(workspace)

    with connect(paths) as connection:
        initialize_db(connection)
        workspace_record = find_workspace(
            connection=connection,
            workspace_path=workspace_path,
            resolved_root_path=resolved_workspace.root_path,
        )
        if workspace_record is None:
            raise RolloutsError(f"workspace is not initialized: {resolved_workspace.root_path}")

        return clear_workspace_remote_url(
            connection,
            workspace_id=workspace_record.id,
        )


def clear_all_workspace_remotes() -> int:
    paths = get_app_paths()
    ensure_app_home(paths)

    with connect(paths) as connection:
        initialize_db(connection)
        return clear_all_workspace_remote_urls(connection)
