from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn

from rollouts.commands.delete import delete_data, validate_delete_args
from rollouts.commands.export import export_opencode_session
from rollouts.commands.push import (
    get_push_workspace_count,
    push_snapshots,
    validate_push_args,
)
from rollouts.commands.remote import (
    clear_all_workspace_remotes,
    clear_workspace_remote,
    set_global_remote_defaults,
    set_workspace_remote,
)
from rollouts.commands.restore import restore_remote_workspace, restore_workspace
from rollouts.commands.snapshot import snapshot_workspace
from rollouts.errors import RolloutsError
from rollouts.github import get_github_repo_web_url
from rollouts.models import WorkspaceRecord

app = typer.Typer(no_args_is_help=True, help="Capture and restore agent rollout workspace states.")
remote_app = typer.Typer(no_args_is_help=True, help="Configure remote archive repositories.")
remote_defaults_app = typer.Typer(
    no_args_is_help=True,
    help="Configure default GitHub archive repo creation settings.",
)
app.add_typer(remote_app, name="remote")
remote_app.add_typer(remote_defaults_app, name="defaults")
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


@remote_app.command("set")
def remote_set(
    workspace: Path = typer.Argument(
        Path("."),
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        resolve_path=True,
        help="Path anywhere inside the source directory to configure.",
    ),
    remote_url: str = typer.Option(
        ...,
        "--url",
        help="GitHub repository URL to use as the workspace archive remote.",
    ),
) -> None:
    """Set the archive remote for a workspace."""

    try:
        workspace_record = set_workspace_remote(
            workspace=workspace,
            remote_url=remote_url,
        )
    except RolloutsError as error:
        error_console.print(f"[red]Error:[/red] {error}")
        raise typer.Exit(code=1) from error

    output_console.print(f"[green]Configured remote[/green] {workspace_record.remote_url}")
    output_console.print(f"workspace: {workspace_record.root_path}")


@remote_app.command("clear")
def remote_clear(
    workspace: Path = typer.Argument(
        Path("."),
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        resolve_path=True,
        help="Path anywhere inside the source directory to clear. Ignored with --all.",
    ),
    clear_all: bool = typer.Option(
        False,
        "--all",
        help="Clear stored remotes for all registered workspaces.",
    ),
) -> None:
    """Clear stored archive remotes without deleting snapshots."""

    try:
        if clear_all:
            cleared_count = clear_all_workspace_remotes()
            output_console.print(f"[green]Cleared workspace remotes[/green] {cleared_count}")
            return

        workspace_record = clear_workspace_remote(workspace=workspace)
    except RolloutsError as error:
        error_console.print(f"[red]Error:[/red] {error}")
        raise typer.Exit(code=1) from error

    output_console.print("[green]Cleared workspace remote[/green]")
    output_console.print(f"workspace: {workspace_record.root_path}")


@remote_defaults_app.command("set")
def remote_defaults_set(
    owner: str = typer.Option(
        ...,
        "--owner",
        help="GitHub user or organization that should own auto-created archive repos.",
    ),
    prefix: str = typer.Option(
        "rollouts-",
        "--prefix",
        help="Prefix to use when deriving auto-created archive repo names.",
    ),
    visibility: str = typer.Option(
        "private",
        "--visibility",
        help="GitHub repo visibility for auto-created archive repos: private, public, or internal.",
    ),
) -> None:
    """Set global defaults for auto-created GitHub archive repos."""

    try:
        defaults = set_global_remote_defaults(
            owner=owner,
            repo_prefix=prefix,
            visibility=visibility,
        )
    except RolloutsError as error:
        error_console.print(f"[red]Error:[/red] {error}")
        raise typer.Exit(code=1) from error

    output_console.print("[green]Configured remote defaults[/green]")
    output_console.print(f"owner: {defaults.owner}")
    output_console.print(f"prefix: {defaults.repo_prefix}")
    output_console.print(f"visibility: {defaults.visibility}")


@app.command()
def restore(
    workspace: Path = typer.Argument(
        Path("."),
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        resolve_path=True,
        help="Path anywhere inside the source directory to restore from. Ignored with --repo.",
    ),
    repo_url: str | None = typer.Option(
        None,
        "--repo",
        help="Remote archive repo URL or GitHub repo page URL.",
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
        if repo_url is not None:
            result = restore_remote_workspace(
                repo_url=repo_url,
                session_id=session_id,
                message_id=message_id,
                destination=destination,
            )
            output_console.print("[green]Restored snapshot[/green]")
            output_console.print(f"repo: {result.repo_url}")
            output_console.print(f"session: {session_id}")
            output_console.print(f"message: {message_id}")
            output_console.print(f"tag: {result.tag_ref}")
            output_console.print(f"store commit: {result.store_commit_sha}")
            output_console.print(f"destination: {destination}")
            return

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
def push(
    workspace: Path = typer.Argument(
        Path("."),
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        resolve_path=True,
        help="Path anywhere inside the source directory to push from. Ignored with --all.",
    ),
    session_id: str | None = typer.Option(
        None,
        "--session",
        help="Push only snapshots for one external chat session.",
    ),
    message_id: str | None = typer.Option(
        None,
        "--message",
        help="Push only one snapshot for the given external message identifier.",
    ),
    push_all: bool = typer.Option(
        False,
        "--all",
        help="Push snapshots for all workspaces with configured remotes.",
    ),
    create_remote: bool = typer.Option(
        False,
        "--create-remote",
        help="Create and remember a GitHub archive repo when a workspace has no configured remote.",
    ),
) -> None:
    """Push stored snapshots to configured archive remotes."""

    try:
        validate_push_args(
            session_id=session_id,
            message_id=message_id,
            push_all=push_all,
            create_remote=create_remote,
        )
        workspace_total = get_push_workspace_count(
            workspace=workspace,
            session_id=session_id,
            message_id=message_id,
            push_all=push_all,
            create_remote=create_remote,
        )
        remote_urls: list[str] = []
        seen_remote_urls: set[str] = set()
        with Progress(
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            console=output_console,
        ) as progress:
            task_id = progress.add_task(
                "Pushing workspaces",
                total=workspace_total,
            )

            def on_workspace_pushed(workspace_record: WorkspaceRecord) -> None:
                progress.update(task_id, advance=1)
                if workspace_record.remote_url is not None:
                    remote_url = get_github_repo_web_url(workspace_record.remote_url)
                    if remote_url not in seen_remote_urls:
                        seen_remote_urls.add(remote_url)
                        remote_urls.append(remote_url)

            result = push_snapshots(
                workspace=workspace,
                session_id=session_id,
                message_id=message_id,
                push_all=push_all,
                create_remote=create_remote,
                on_workspace_pushed=on_workspace_pushed,
            )
    except RolloutsError as error:
        error_console.print(f"[red]Error:[/red] {error}")
        raise typer.Exit(code=1) from error

    output_console.print(f"[green]Pushed snapshots[/green] {result.pushed_snapshots}")
    output_console.print(f"skipped existing: {result.skipped_snapshots}")
    output_console.print(f"workspaces: {result.workspace_count}")
    output_console.print(f"created remotes: {result.created_remotes}")
    if remote_urls:
        output_console.print("")
        output_console.print("Remote URLs")
        for remote_url in remote_urls:
            output_console.print(f"[link={remote_url}]{remote_url}[/link]")


@app.command()
def export(
    agent: str = typer.Option(
        ...,
        "--agent",
        help="Agent source to export. Currently only `opencode` is supported.",
    ),
    session_id: str = typer.Option(..., "--session", help="Session identifier."),
    output_path: Path = typer.Option(
        ...,
        "--out",
        resolve_path=True,
        help="Output JSON file path.",
    ),
) -> None:
    """Export agent session data for downstream use."""

    if agent != "opencode":
        error_console.print(f"[red]Error:[/red] unsupported agent: {agent}")
        raise typer.Exit(code=1)

    try:
        result = export_opencode_session(
            session_id=session_id,
            output_path=output_path,
        )
    except RolloutsError as error:
        error_console.print(f"[red]Error:[/red] {error}")
        raise typer.Exit(code=1) from error

    output_console.print("[green]Exported session[/green]")
    output_console.print(f"agent: {agent}")
    output_console.print(f"session: {result.session_id}")
    output_console.print(f"title: {result.title}")
    output_console.print(f"messages: {result.message_count}")
    remote_url = None if result.metadata is None else result.metadata["remote_url"]
    output_console.print(f"remote url: {remote_url if remote_url is not None else 'none'}")
    output_console.print(f"output: {result.output_path}")


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
