from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console

from rollouts.commands.delete import delete_data, validate_delete_args
from rollouts.commands.restore import restore_workspace
from rollouts.commands.snapshot import snapshot_workspace
from rollouts.errors import RolloutsError

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


@app.command()
def delete(
    workspace: Path = typer.Argument(
        Path("."),
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        resolve_path=True,
        help="Path anywhere inside the source directory to delete from. Ignored with --all.",
    ),
    session_id: str | None = typer.Option(
        None,
        "--session",
        help="External chat session identifier.",
    ),
    message_id: str | None = typer.Option(
        None,
        "--message",
        help="External message identifier.",
    ),
    delete_all: bool = typer.Option(
        False,
        "--all",
        help="Delete all local Rollouts data.",
    ),
) -> None:
    """Delete stored Rollouts data after confirmation."""

    try:
        validate_delete_args(
            session_id=session_id,
            message_id=message_id,
            delete_all=delete_all,
        )
        confirmed = typer.confirm(
            _build_delete_confirmation(
                workspace=workspace,
                session_id=session_id,
                message_id=message_id,
                delete_all=delete_all,
            ),
            default=False,
        )
        if not confirmed:
            output_console.print("Cancelled.")
            raise typer.Exit(code=1)

        result = delete_data(
            workspace=workspace,
            session_id=session_id,
            message_id=message_id,
            delete_all=delete_all,
        )
    except RolloutsError as error:
        error_console.print(f"[red]Error:[/red] {error}")
        raise typer.Exit(code=1) from error

    if result.deleted_all:
        output_console.print(f"[green]Deleted all Rollouts data[/green] {result.deleted_path}")
        return

    if result.deleted_workspace:
        output_console.print(f"[green]Deleted workspace data[/green] {result.deleted_path}")
        output_console.print(f"snapshots deleted: {result.deleted_snapshots}")
        return

    output_console.print(f"[green]Deleted snapshots[/green] {result.deleted_snapshots}")
    if session_id is not None:
        output_console.print(f"session: {session_id}")
    if message_id is not None:
        output_console.print(f"message: {message_id}")


def _build_delete_confirmation(
    *,
    workspace: Path,
    session_id: str | None,
    message_id: str | None,
    delete_all: bool,
) -> str:
    if delete_all:
        return "Delete ALL local Rollouts data? This cannot be undone."

    if session_id is None:
        return (
            f"Delete all stored Rollouts data for workspace {workspace.resolve(strict=False)}? "
            "This cannot be undone."
        )

    if message_id is None:
        return (
            f"Delete all snapshots for session {session_id!r} in "
            f"{workspace.resolve(strict=False)}? This cannot be undone."
        )

    return (
        f"Delete the snapshot for session {session_id!r} and message {message_id!r} in "
        f"{workspace.resolve(strict=False)}? This cannot be undone."
    )
