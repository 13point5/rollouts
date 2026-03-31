from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from rollouts.errors import RolloutsError
from rollouts.models import ResolvedWorkspace, SnapshotRecord


@dataclass(frozen=True)
class PushTagResult:
    tag_ref: str
    pushed: bool


@dataclass(frozen=True)
class RemoteRestoreResult:
    repo_url: str
    tag_ref: str
    store_commit_sha: str


def resolve_workspace_source(workspace: Path) -> ResolvedWorkspace:
    workspace_path = workspace.resolve(strict=False)
    git_root = _try_resolve_git_workspace_root(workspace_path)
    if git_root is not None:
        return ResolvedWorkspace(
            root_path=git_root,
            is_git=True,
            vcs=_build_git_vcs_metadata(workspace_path=workspace_path, git_root=git_root),
        )

    return ResolvedWorkspace(
        root_path=workspace_path,
        is_git=False,
        vcs=json.dumps({"vcs": None}),
    )


def initialize_bare_store(store_path: Path) -> None:
    store_path.parent.mkdir(parents=True, exist_ok=True)
    _run_git(["init", "--bare", str(store_path)])


def create_snapshot_commit(
    *,
    workspace_root: Path,
    is_git: bool,
    store_path: Path,
    snapshot_id: str,
    session_id: str,
    message_id: str,
    excluded_paths: Sequence[Path] = (),
) -> str:
    with tempfile.TemporaryDirectory(prefix="rollouts-snapshot-") as tmp_dir:
        staging_path = Path(tmp_dir) / "staging"
        _run_git(["clone", "--quiet", str(store_path), str(staging_path)])
        _run_git(["-C", str(staging_path), "config", "user.name", "rollouts"])
        _run_git(["-C", str(staging_path), "config", "user.email", "rollouts@local"])

        _clear_worktree(staging_path)
        _copy_workspace_files(
            workspace_root=workspace_root,
            is_git=is_git,
            staging_path=staging_path,
            excluded_paths=excluded_paths,
        )

        _run_git(["-C", str(staging_path), "add", "-A"])
        _run_git(
            [
                "-C",
                str(staging_path),
                "commit",
                "--allow-empty",
                "-m",
                f"snapshot {session_id} message {message_id}",
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


def build_snapshot_tag_ref(*, session_id: str, message_id: str) -> str:
    session_hash = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    message_hash = hashlib.sha256(message_id.encode("utf-8")).hexdigest()
    return f"refs/tags/rollouts/session/{session_hash}/message/{message_hash}"


def snapshot_tag_exists_on_remote(*, remote_url: str, session_id: str, message_id: str) -> bool:
    tag_ref = build_snapshot_tag_ref(session_id=session_id, message_id=message_id)
    return _remote_ref_exists(remote_url=remote_url, ref_name=tag_ref)


def push_snapshot_tag(
    *,
    store_path: Path,
    remote_url: str,
    snapshot: SnapshotRecord,
) -> PushTagResult:
    tag_ref = build_snapshot_tag_ref(
        session_id=snapshot.session_id,
        message_id=snapshot.message_id,
    )
    if _remote_ref_exists(remote_url=remote_url, ref_name=tag_ref):
        return PushTagResult(tag_ref=tag_ref, pushed=False)

    tag_name = tag_ref.removeprefix("refs/tags/")
    tag_message = _build_snapshot_tag_message(snapshot)
    push_error: Exception | None = None
    tag_created = False

    try:
        _run_git(
            [
                "--git-dir",
                str(store_path),
                "tag",
                "-a",
                tag_name,
                snapshot.store_commit_sha,
                "-m",
                tag_message,
            ]
        )
        tag_created = True
        _run_git(
            [
                "--git-dir",
                str(store_path),
                "push",
                remote_url,
                f"{tag_ref}:{tag_ref}",
            ]
        )
    except Exception as error:
        push_error = error
        raise
    finally:
        if tag_created:
            try:
                _delete_local_tag(store_path=store_path, tag_name=tag_name)
            except RolloutsError:
                if push_error is None:
                    raise

    return PushTagResult(tag_ref=tag_ref, pushed=True)


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


def restore_remote_snapshot_to_destination(
    *,
    repo_url: str,
    session_id: str,
    message_id: str,
    destination: Path,
) -> RemoteRestoreResult:
    tag_ref = build_snapshot_tag_ref(session_id=session_id, message_id=message_id)
    if not _remote_ref_exists(remote_url=repo_url, ref_name=tag_ref):
        raise RolloutsError(
            f"no remote snapshot found for session {session_id!r} and message {message_id!r}"
        )

    tag_name = tag_ref.removeprefix("refs/tags/")
    with tempfile.TemporaryDirectory(prefix="rollouts-remote-restore-") as tmp_dir:
        store_path = Path(tmp_dir) / "store.git"
        initialize_bare_store(store_path)
        _run_git(
            [
                "--git-dir",
                str(store_path),
                "fetch",
                "--quiet",
                repo_url,
                f"{tag_ref}:{tag_ref}",
            ]
        )
        store_commit_sha = _run_git(
            ["--git-dir", str(store_path), "rev-parse", f"{tag_name}^{{}}"]
        ).stdout.strip()
        restore_snapshot_to_destination(
            store_path=store_path,
            destination=destination,
            store_commit_sha=store_commit_sha,
        )

    return RemoteRestoreResult(
        repo_url=repo_url,
        tag_ref=tag_ref,
        store_commit_sha=store_commit_sha,
    )


def _clear_worktree(staging_path: Path) -> None:
    for child in staging_path.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir():
            import shutil

            shutil.rmtree(child)
        else:
            child.unlink()


def _copy_workspace_files(
    *,
    workspace_root: Path,
    is_git: bool,
    staging_path: Path,
    excluded_paths: Sequence[Path],
) -> None:
    if is_git:
        _copy_git_workspace_files(
            workspace_root=workspace_root,
            staging_path=staging_path,
            excluded_paths=excluded_paths,
        )
        return

    _copy_directory_workspace_files(
        source_root=workspace_root,
        destination_root=staging_path,
        excluded_paths=excluded_paths,
    )


def _copy_git_workspace_files(
    *,
    workspace_root: Path,
    staging_path: Path,
    excluded_paths: Sequence[Path],
) -> None:
    import shutil

    completed = _run_git_bytes(
        ["-C", str(workspace_root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    )
    relative_paths = [item.decode("utf-8") for item in completed.stdout.split(b"\0") if item]

    for relative_path in relative_paths:
        source_path = workspace_root / relative_path
        if _should_skip_path(source_path, excluded_paths) or source_path.name == ".git":
            continue
        destination_path = staging_path / relative_path
        destination_path.parent.mkdir(parents=True, exist_ok=True)

        if source_path.is_symlink():
            target = os.readlink(source_path)
            destination_path.symlink_to(target)
        elif source_path.is_file():
            shutil.copy2(source_path, destination_path)
        else:
            raise RolloutsError(f"unsupported tracked path type: {source_path}")


def _copy_directory_workspace_files(
    *,
    source_root: Path,
    destination_root: Path,
    excluded_paths: Sequence[Path],
) -> None:
    import shutil

    for source_path in sorted(source_root.iterdir(), key=lambda path: path.name):
        if source_path.name == ".git" or _should_skip_path(source_path, excluded_paths):
            continue

        destination_path = destination_root / source_path.name
        if source_path.is_symlink():
            target = os.readlink(source_path)
            destination_path.symlink_to(target)
        elif source_path.is_file():
            shutil.copy2(source_path, destination_path)
        elif source_path.is_dir():
            destination_path.mkdir(parents=True, exist_ok=True)
            _copy_directory_workspace_files(
                source_root=source_path,
                destination_root=destination_path,
                excluded_paths=excluded_paths,
            )
        else:
            raise RolloutsError(f"unsupported workspace path type: {source_path}")


def _should_skip_path(path: Path, excluded_paths: Sequence[Path]) -> bool:
    resolved_path = path.resolve(strict=False)
    return any(
        resolved_path == excluded_path or excluded_path in resolved_path.parents
        for excluded_path in excluded_paths
    )


def _run_git(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            ["git", *args],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as error:
        raise RolloutsError("git executable not found") from error

    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise RolloutsError(message)

    return completed


def _run_git_bytes(args: list[str]) -> subprocess.CompletedProcess[bytes]:
    try:
        completed = subprocess.run(
            ["git", *args],
            check=False,
            capture_output=True,
            text=False,
        )
    except FileNotFoundError as error:
        raise RolloutsError("git executable not found") from error

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


def _try_resolve_git_workspace_root(workspace: Path) -> Path | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as error:
        raise RolloutsError("git executable not found") from error

    if completed.returncode != 0:
        return None

    return Path(completed.stdout.strip()).resolve()


def _build_git_vcs_metadata(*, workspace_path: Path, git_root: Path) -> str:
    branch = _run_git_optional_text(
        ["-C", str(workspace_path), "symbolic-ref", "--quiet", "--short", "HEAD"]
    )
    head_commit = _run_git_optional_text(["-C", str(workspace_path), "rev-parse", "HEAD"])

    return json.dumps(
        {
            "vcs": "git",
            "worktree_path": str(git_root),
            "branch": branch,
            "head_commit": head_commit,
        }
    )


def _run_git_optional_text(args: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as error:
        raise RolloutsError("git executable not found") from error

    if completed.returncode != 0:
        return None

    return completed.stdout.strip() or None


def _build_snapshot_tag_message(snapshot: SnapshotRecord) -> str:
    payload = {
        "schema_version": 1,
        "snapshot_id": snapshot.id,
        "session_id": snapshot.session_id,
        "message_id": snapshot.message_id,
        "captured_at": snapshot.captured_at.isoformat(),
        "store_commit_sha": snapshot.store_commit_sha,
        "vcs": json.loads(snapshot.vcs),
        "metadata": json.loads(snapshot.metadata),
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def _delete_local_tag(*, store_path: Path, tag_name: str) -> None:
    completed = subprocess.run(
        ["git", "--git-dir", str(store_path), "tag", "-d", tag_name],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode not in (0, 1):
        message = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise RolloutsError(message)


def _remote_ref_exists(*, remote_url: str, ref_name: str) -> bool:
    completed = _run_git(["ls-remote", "--refs", remote_url, ref_name])
    return bool(completed.stdout.strip())
