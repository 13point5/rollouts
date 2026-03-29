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
    created_at: datetime


class WorkspaceInitResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    workspace: WorkspaceRecord
    created: bool
