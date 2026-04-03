from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict


class AppPaths(BaseModel):
    model_config = ConfigDict(frozen=True)

    home: Path
    db_path: Path
    workspaces_dir: Path


class WorkspaceRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    root_path: Path
    store_path: Path
    remote_url: str | None = None
    created_at: datetime


class RemoteDefaultsRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    owner: str
    repo_prefix: str
    visibility: str


class ResolvedWorkspace(BaseModel):
    model_config = ConfigDict(frozen=True)

    root_path: Path
    is_git: bool
    vcs: str


class SnapshotRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    workspace_id: str
    session_id: str
    message_id: str
    store_commit_sha: str
    vcs: str
    metadata: str
    captured_at: datetime


class LearnSessionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    workspace_id: str
    session_name: str
    dataset_repo: str
    prime_config: str
    created_at: datetime
    updated_at: datetime


class LearnRunRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    session_id: str
    run_number: int
    prime_run_id: str | None = None
    prime_checkpoint_id: str | None = None
    prime_model_id: str | None = None
    prime_config: str
    created_at: datetime
    updated_at: datetime
