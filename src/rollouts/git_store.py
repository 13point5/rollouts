from __future__ import annotations

import subprocess
from pathlib import Path

from rollouts.errors import RolloutsError


def resolve_git_workspace_root(workspace: Path) -> Path:
    completed = _run_git(["-C", str(workspace), "rev-parse", "--show-toplevel"])
    return Path(completed.stdout.strip()).resolve()


def initialize_bare_store(store_path: Path) -> None:
    store_path.parent.mkdir(parents=True, exist_ok=True)
    _run_git(["init", "--bare", str(store_path)])


def _run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", *args],
        check=False,
        capture_output=True,
        text=True,
    )

    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise RolloutsError(message)

    return completed
