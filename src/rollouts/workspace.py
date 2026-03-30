from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from rollouts.db import (
    connect,
    create_workspace,
    find_workspace,
    initialize_db,
    update_workspace_root_path,
)
from rollouts.git_store import initialize_bare_store, resolve_workspace_source
from rollouts.models import ResolvedWorkspace, WorkspaceRecord
from rollouts.paths import ensure_app_home, get_app_paths, workspace_store_path


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
