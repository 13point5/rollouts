from __future__ import annotations

import io
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path

from rollouts.errors import RolloutsError


def resolve_git_workspace_root(workspace: Path) -> Path:
    completed = _run_git(["-C", str(workspace), "rev-parse", "--show-toplevel"])
    return Path(completed.stdout.strip()).resolve()


def initialize_bare_store(store_path: Path) -> None:
    store_path.parent.mkdir(parents=True, exist_ok=True)
    _run_git(["init", "--bare", str(store_path)])


def create_snapshot_commit(
    *,
    workspace_root: Path,
    store_path: Path,
    snapshot_id: str,
    session_id: str,
    turn_id: str,
) -> str:
    with tempfile.TemporaryDirectory(prefix="rollouts-snapshot-") as tmp_dir:
        staging_path = Path(tmp_dir) / "staging"
        _run_git(["clone", "--quiet", str(store_path), str(staging_path)])
        _run_git(["-C", str(staging_path), "config", "user.name", "rollouts"])
        _run_git(["-C", str(staging_path), "config", "user.email", "rollouts@local"])

        _clear_worktree(staging_path)
        _copy_visible_workspace_files(workspace_root, staging_path)

        _run_git(["-C", str(staging_path), "add", "-A"])
        _run_git(
            [
                "-C",
                str(staging_path),
                "commit",
                "--allow-empty",
                "-m",
                f"snapshot {session_id} turn {turn_id}",
            ]
        )

        commit_sha = _run_git(["-C", str(staging_path), "rev-parse", "HEAD"]).stdout.strip()
        _run_git(
            [
                "-C",
                str(staging_path),
                "push",
                "origin",
                f"HEAD:refs/snapshots/{snapshot_id}",
            ]
        )
        return commit_sha


def delete_snapshot_ref(*, store_path: Path, snapshot_id: str) -> None:
    completed = subprocess.run(
        ["git", "--git-dir", str(store_path), "update-ref", "-d", f"refs/snapshots/{snapshot_id}"],
        check=False,
        capture_output=True,
        text=True,
    )

    if completed.returncode not in (0, 1):
        message = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise RolloutsError(message)


def restore_snapshot_to_destination(
    *,
    store_path: Path,
    destination: Path,
    store_commit_sha: str,
) -> None:
    if destination.exists():
        raise RolloutsError(f"destination already exists: {destination}")

    archive = _run_git_bytes(
        ["--git-dir", str(store_path), "archive", "--format=tar", store_commit_sha]
    ).stdout

    destination.mkdir(parents=True, exist_ok=False)
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
            _extract_tar_into_destination(tar, destination)
    except Exception:
        import shutil

        shutil.rmtree(destination, ignore_errors=True)
        raise


def _clear_worktree(staging_path: Path) -> None:
    for child in staging_path.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir():
            import shutil

            shutil.rmtree(child)
        else:
            child.unlink()


def _copy_visible_workspace_files(workspace_root: Path, staging_path: Path) -> None:
    import shutil

    completed = _run_git_bytes(
        ["-C", str(workspace_root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    )
    relative_paths = [item.decode("utf-8") for item in completed.stdout.split(b"\0") if item]

    for relative_path in relative_paths:
        source_path = workspace_root / relative_path
        destination_path = staging_path / relative_path
        destination_path.parent.mkdir(parents=True, exist_ok=True)

        if source_path.is_symlink():
            target = os.readlink(source_path)
            destination_path.symlink_to(target)
        elif source_path.is_file():
            shutil.copy2(source_path, destination_path)
        else:
            raise RolloutsError(f"unsupported tracked path type: {source_path}")


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


def _run_git_bytes(args: list[str]) -> subprocess.CompletedProcess[bytes]:
    completed = subprocess.run(
        ["git", *args],
        check=False,
        capture_output=True,
        text=False,
    )

    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        stdout = completed.stdout.decode("utf-8", errors="replace").strip()
        message = stderr or stdout or "git command failed"
        raise RolloutsError(message)

    return completed


def _extract_tar_into_destination(tar: tarfile.TarFile, destination: Path) -> None:
    for member in tar.getmembers():
        member_path = destination / member.name
        resolved_member_path = member_path.resolve(strict=False)
        if destination not in resolved_member_path.parents and resolved_member_path != destination:
            raise RolloutsError(f"refusing to extract path outside destination: {member.name}")

    tar.extractall(destination)
