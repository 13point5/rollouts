from __future__ import annotations

import os
from pathlib import Path

from rollouts.models import AppPaths

ROLLOUTS_HOME_ENV = "ROLLOUTS_HOME"
DEFAULT_ROLLOUTS_HOME = Path("~/.rollouts")


def get_app_paths() -> AppPaths:
    configured_home = os.environ.get(ROLLOUTS_HOME_ENV)

    home = (
        Path(configured_home).expanduser()
        if configured_home
        else DEFAULT_ROLLOUTS_HOME.expanduser()
    )
    resolved_home = home.resolve(strict=False)

    return AppPaths(
        home=resolved_home,
        db_path=resolved_home / "rollouts.sqlite",
        workspaces_dir=resolved_home / "workspaces",
    )


def ensure_app_home(paths: AppPaths) -> None:
    paths.home.mkdir(parents=True, exist_ok=True)
    paths.workspaces_dir.mkdir(parents=True, exist_ok=True)


def workspace_store_path(paths: AppPaths, workspace_id: str) -> Path:
    return paths.workspaces_dir / workspace_id / "store.git"
