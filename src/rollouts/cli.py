from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from rollouts.errors import RolloutsError
from rollouts.snapshot import snapshot_workspace
from rollouts.workspace import ensure_workspace

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
        result = ensure_workspace(workspace)
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


@app.command()
def snapshot(
    workspace: Path = typer.Argument(
        Path("."),
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        resolve_path=True,
        help="Path anywhere inside the Git workspace you want to snapshot.",
    ),
    session_id: str = typer.Option(..., "--session", help="External chat session identifier."),
    turn_id: str = typer.Option(..., "--turn", help="External turn identifier."),
    metadata: str = typer.Option(
        ...,
        "--metadata",
        help="Inline metadata JSON string.",
    ),
) -> None:
    """Store a workspace snapshot for a session turn."""

    try:
        record = snapshot_workspace(
            workspace=workspace,
            session_id=session_id,
            turn_id=turn_id,
            metadata=metadata,
        )
    except RolloutsError as error:
        console.print(f"[red]Error:[/red] {error}")
        raise typer.Exit(code=1) from error

    output = Console()
    output.print(f"[green]Created snapshot[/green] {record.id}")
    output.print(f"session: {record.session_id}")
    output.print(f"turn: {record.turn_id}")
    output.print(f"store commit: {record.store_commit_sha}")
    output.print(f"captured at: {record.captured_at.isoformat()}")
