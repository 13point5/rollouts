from __future__ import annotations

import shutil
from pathlib import Path
from uuid import uuid4

from rollouts.db import connect, create_workspace, get_workspace_by_root_path, initialize_db
from rollouts.git_store import initialize_bare_store, resolve_git_workspace_root
from rollouts.models import WorkspaceInitResult
from rollouts.paths import ensure_app_home, get_app_paths, workspace_store_path


def ensure_workspace(workspace: Path) -> WorkspaceInitResult:
    paths = get_app_paths()
    ensure_app_home(paths)
    workspace_root = resolve_git_workspace_root(workspace)

    with connect(paths) as connection:
        initialize_db(connection)
        existing = get_workspace_by_root_path(connection, workspace_root)
        if existing is not None:
            return WorkspaceInitResult(workspace=existing, created=False)

        workspace_id = uuid4().hex
        store_path = workspace_store_path(paths, workspace_id=workspace_id)
        try:
            initialize_bare_store(store_path)
            record = create_workspace(
                connection,
                workspace_id=workspace_id,
                root_path=workspace_root,
                store_path=store_path,
            )
            return WorkspaceInitResult(workspace=record, created=True)
        except Exception:
            shutil.rmtree(store_path.parent, ignore_errors=True)
            raise
