from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from rollouts.errors import RolloutsError
from rollouts.restore import restore_workspace
from rollouts.snapshot import snapshot_workspace

app = typer.Typer(no_args_is_help=True, help="Capture and restore agent rollout workspace states.")
output_console = Console()
error_console = Console(stderr=True)


@app.callback()
def main() -> None:
    """Rollouts command group."""


@app.command()
def snapshot(
    workspace: Path = typer.Argument(
        Path("."),
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        resolve_path=True,
        help="Path anywhere inside the directory you want to snapshot.",
    ),
    session_id: str = typer.Option(..., "--session", help="External chat session identifier."),
    message_id: str = typer.Option(..., "--message", help="External message identifier."),
    metadata: str = typer.Option(
        ...,
        "--metadata",
        help="Inline metadata JSON string.",
    ),
) -> None:
    """Store a workspace snapshot for a session message."""

    try:
        record = snapshot_workspace(
            workspace=workspace,
            session_id=session_id,
            message_id=message_id,
            metadata=metadata,
        )
    except RolloutsError as error:
        error_console.print(f"[red]Error:[/red] {error}")
        raise typer.Exit(code=1) from error

    output_console.print(f"[green]Created snapshot[/green] {record.id}")
    output_console.print(f"session: {record.session_id}")
    output_console.print(f"message: {record.message_id}")
    output_console.print(f"store commit: {record.store_commit_sha}")
    output_console.print(f"captured at: {record.captured_at.isoformat()}")


@app.command()
def restore(
    workspace: Path = typer.Argument(
        Path("."),
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        resolve_path=True,
        help="Path anywhere inside the source directory to restore from.",
    ),
    session_id: str = typer.Option(..., "--session", help="External chat session identifier."),
    message_id: str = typer.Option(..., "--message", help="External message identifier."),
    destination: Path = typer.Option(
        ...,
        "--dest",
        resolve_path=True,
        help="Destination directory for the restored snapshot.",
    ),
) -> None:
    """Restore the snapshot for a session message into a new directory."""

    try:
        record = restore_workspace(
            workspace=workspace,
            session_id=session_id,
            message_id=message_id,
            destination=destination,
        )
    except RolloutsError as error:
        error_console.print(f"[red]Error:[/red] {error}")
        raise typer.Exit(code=1) from error

    output_console.print(f"[green]Restored snapshot[/green] {record.id}")
    output_console.print(f"session: {record.session_id}")
    output_console.print(f"message: {record.message_id}")
    output_console.print(f"store commit: {record.store_commit_sha}")
    output_console.print(f"destination: {destination}")
