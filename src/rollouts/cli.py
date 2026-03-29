from __future__ import annotations

import shutil
from pathlib import Path

import typer
from rich.console import Console

from rollouts.db import connect, create_workspace, get_workspace_by_root_path, initialize_db
from rollouts.errors import RolloutsError
from rollouts.git_store import initialize_bare_store, resolve_git_workspace_root
from rollouts.models import WorkspaceInitResult
from rollouts.paths import ensure_app_home, get_app_paths, workspace_store_path

app = typer.Typer(no_args_is_help=True, help="Capture and restore agent rollout workspace states.")
console = Console(stderr=True)


@app.callback()
def main() -> None:
    """Rollouts command group."""


@app.command()
def init(
    workspace: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        resolve_path=True,
        help="Path anywhere inside the Git workspace you want to track.",
    ),
) -> None:
    """Register a workspace and create its bare Git store."""

    try:
        result = initialize_workspace(workspace)
    except RolloutsError as error:
        console.print(f"[red]Error:[/red] {error}")
        raise typer.Exit(code=1) from error

    output = Console()
    verb = (
        "[green]Initialized workspace[/green]"
        if result.created
        else "[yellow]Workspace already registered[/yellow]"
    )
    record = result.workspace
    output.print(f"{verb} {record.id}")
    output.print(f"root: {record.root_path}")
    output.print(f"store: {record.store_path}")


def initialize_workspace(workspace: Path) -> WorkspaceInitResult:
    from uuid import uuid4

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
