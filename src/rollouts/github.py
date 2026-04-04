from __future__ import annotations

import re
import shutil
import subprocess

from rollouts.errors import RolloutsError
from rollouts.models import RemoteDefaultsRecord, WorkspaceRecord


def ensure_github_cli_available() -> None:
    if shutil.which("gh") is None:
        raise RolloutsError("gh executable not found")


def create_github_repo(
    *,
    workspace: WorkspaceRecord,
    defaults: RemoteDefaultsRecord,
) -> str:
    ensure_github_cli_available()
    repo_name = build_github_repo_name(workspace=workspace, prefix=defaults.repo_prefix)
    repo_full_name = f"{defaults.owner}/{repo_name}"

    _run_gh(
        [
            "repo",
            "create",
            repo_full_name,
            f"--{defaults.visibility}",
            "--disable-issues",
            "--disable-wiki",
        ]
    )

    protocol = get_github_git_protocol()
    if protocol == "ssh":
        return f"git@github.com:{repo_full_name}.git"
    return f"https://github.com/{repo_full_name}.git"


def build_github_repo_name(*, workspace: WorkspaceRecord, prefix: str) -> str:
    slug = _slugify(workspace.root_path.name) or "workspace"
    return f"{prefix}{slug}-{workspace.id[:8]}"


def get_github_git_protocol() -> str:
    ensure_github_cli_available()
    completed = subprocess.run(
        ["gh", "config", "get", "git_protocol"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        return "https"

    protocol = completed.stdout.strip()
    if protocol in {"https", "ssh"}:
        return protocol
    return "https"


def get_github_repo_web_url(remote_url: str) -> str:
    ssh_match = re.fullmatch(r"git@github\.com:([^/]+/[^/]+)\.git", remote_url)
    if ssh_match is not None:
        return f"https://github.com/{ssh_match.group(1)}"

    https_match = re.fullmatch(r"https://github\.com/([^/]+/[^/]+?)(?:\.git)?", remote_url)
    if https_match is not None:
        return f"https://github.com/{https_match.group(1)}"

    return remote_url


def normalize_github_repo_clone_url(repo_url: str) -> str:
    repo_url = repo_url.strip()

    ssh_match = re.fullmatch(r"git@github\.com:[^/]+/[^/]+\.git", repo_url)
    if ssh_match is not None:
        return repo_url

    https_git_match = re.fullmatch(r"https://github\.com/[^/]+/[^/]+\.git", repo_url)
    if https_git_match is not None:
        return repo_url

    https_web_match = re.fullmatch(r"https://github\.com/([^/]+/[^/]+)/?", repo_url)
    if https_web_match is not None:
        return f"https://github.com/{https_web_match.group(1)}.git"

    return repo_url


def _run_gh(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            ["gh", *args],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as error:
        raise RolloutsError("gh executable not found") from error

    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "gh command failed"
        raise RolloutsError(message)

    return completed


def _slugify(value: str) -> str:
    value = value.lower().strip()
    slug = re.sub(r"[^a-z0-9]+", "-", value)
    return slug.strip("-")
