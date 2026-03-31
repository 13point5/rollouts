from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from rollouts.errors import RolloutsError
from rollouts.models import ResolvedWorkspace, WorkspaceRecord
from rollouts.paths import ensure_app_home, get_app_paths, workspace_store_path
from rollouts.storage.db import (
    connect,
    create_workspace,
    find_workspace,
    initialize_db,
    update_workspace_root_path,
)
from rollouts.storage.git_store import initialize_bare_store, resolve_workspace_source


def get_existing_workspace(
    workspace: Path,
    *,
    ensure_home: bool = True,
) -> WorkspaceRecord:
    paths = get_app_paths()
    if ensure_home:
        ensure_app_home(paths)
    elif not paths.db_path.exists():
        raise RolloutsError("no rollouts data found")

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

        return workspace_record


def ensure_workspace(
    workspace: Path,
    *,
    resolved_workspace: ResolvedWorkspace | None = None,
) -> WorkspaceRecord:
    paths = get_app_paths()
    ensure_app_home(paths)
    workspace_path = workspace.resolve(strict=False)
    resolved_workspace = resolved_workspace or resolve_workspace_source(workspace)

    with connect(paths) as connection:
        initialize_db(connection)
        existing = find_workspace(
            connection=connection,
            workspace_path=workspace_path,
            resolved_root_path=resolved_workspace.root_path,
        )
        if existing is not None:
            if existing.root_path != resolved_workspace.root_path:
                return update_workspace_root_path(
                    connection,
                    workspace_id=existing.id,
                    root_path=resolved_workspace.root_path,
                )
            return existing

        workspace_id = uuid4().hex
        store_path = workspace_store_path(paths, workspace_id=workspace_id)
        try:
            initialize_bare_store(store_path)
            return create_workspace(
                connection,
                workspace_id=workspace_id,
                root_path=resolved_workspace.root_path,
                store_path=store_path,
            )
        except Exception:
            shutil.rmtree(store_path.parent, ignore_errors=True)
            raise
