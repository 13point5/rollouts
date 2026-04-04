from __future__ import annotations

import tomllib
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, TextColumn, TimeElapsedColumn
from rich.syntax import Syntax
from rich.table import Table

from rollouts.commands.delete import delete_data, validate_delete_args
from rollouts.commands.export import export_opencode_session, export_opencode_sessions_jsonl
from rollouts.commands.hf import push_opencode_exports_to_hf
from rollouts.commands.learn import (
    create_initial_learn_run,
    create_learn_session,
    create_restarted_learn_run,
    delete_learn_session_by_name,
    get_learn_session_status,
    list_learn_session_statuses,
    record_prime_run_id_for_learn_run,
    resolve_learn_run_restart_inputs,
    suggest_dataset_repo_id,
)
from rollouts.commands.list import list_all_sessions, list_all_workspaces
from rollouts.commands.prime import (
    get_prime_rl_run_logs,
    get_prime_rl_run_status,
    start_prime_rl_run,
)
from rollouts.commands.push import (
    PushResult,
    get_push_scope_counts,
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
from rollouts.commands.setup import install_opencode_plugin, validate_setup_scope
from rollouts.commands.snapshot import snapshot_workspace
from rollouts.errors import RolloutsError
from rollouts.github import get_github_repo_web_url
from rollouts.models import RemoteDefaultsRecord, SnapshotRecord, WorkspaceRecord
from rollouts.paths import ensure_app_home, get_app_paths
from rollouts.storage.db import connect, get_remote_defaults, initialize_db

CLI_TITLE = "Rollouts"
CLI_DESCRIPTION = (
    "A CLI for Continual Learning with your own coding agent sessions. "
    "Track rollouts and codebase snapshots at every turn. "
)

app = typer.Typer(no_args_is_help=False, help="Rollouts commands.")
remote_app = typer.Typer(
    no_args_is_help=True,
    help="Configure GitHub repos for tracked workspaces.",
)
remote_defaults_app = typer.Typer(
    no_args_is_help=True,
    help="Configure default GitHub repo creation settings.",
)
hf_app = typer.Typer(no_args_is_help=True, help="Upload rollout exports to Hugging Face datasets.")
learn_app = typer.Typer(no_args_is_help=True, help="Manage global continual-learning sessions.")
app.add_typer(remote_app, name="remote")
remote_app.add_typer(remote_defaults_app, name="defaults")
app.add_typer(hf_app, name="hf")
app.add_typer(learn_app, name="learn")
output_console = Console()
error_console = Console(stderr=True)


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """Rollouts command group."""

    if ctx.invoked_subcommand is not None:
        return

    output_console.print(
        Panel.fit(
            "\n".join(
                [
                    f"[bold]{CLI_TITLE}[/bold] [cyan]v{_get_cli_version()}[/cyan]",
                    "",
                    CLI_DESCRIPTION,
                ]
            ),
            border_style="blue",
            padding=(1, 2),
        )
    )
    output_console.print(ctx.get_help())
    raise typer.Exit()


@app.command()
def setup(
    workspace: Path = typer.Argument(
        Path("."),
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        resolve_path=True,
        help="Project path for --scope project. Ignored for --scope global.",
    ),
    scope: str | None = typer.Option(
        None,
        "--scope",
        help="Install scope for the OpenCode plugin: global or project.",
    ),
) -> None:
    """Install the OpenCode Rollouts plugin."""

    try:
        resolved_scope = _prompt_setup_scope() if scope is None else validate_setup_scope(scope)
        result = install_opencode_plugin(
            scope=resolved_scope,
            workspace=workspace,
        )
        remote_defaults = _ensure_remote_defaults_configured_for_setup()
    except RolloutsError as error:
        error_console.print(f"[red]Error:[/red] {error}")
        raise typer.Exit(code=1) from error

    output_console.print("[green]Installed OpenCode plugin[/green]")
    output_console.print(f"scope: {result.scope}")
    output_console.print(f"path: {result.plugin_path}")
    output_console.print(f"replaced existing: {'yes' if result.replaced_existing else 'no'}")
    output_console.print("")
    output_console.print("[green]GitHub repo defaults[/green]")
    output_console.print(f"owner: {remote_defaults.owner}")
    output_console.print(f"prefix: {remote_defaults.repo_prefix}")
    output_console.print(f"visibility: {remote_defaults.visibility}")


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


@learn_app.command("start")
def learn_start(
    session_name: str = typer.Argument(..., help="Global learn session name."),
    dataset: str | None = typer.Option(
        None,
        "--dataset",
        help="Dataset repo name, repo id, or Hugging Face dataset URL for the learn session.",
    ),
    config: Path = typer.Option(
        ...,
        "--config",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
        help="Prime config TOML file to store for the learn session.",
    ),
) -> None:
    """Create a new global learn session."""

    created_session_name: str | None = None
    try:
        if dataset is None:
            dataset = typer.prompt(
                "Dataset repo",
                default=suggest_dataset_repo_id(session_name=session_name),
                show_default=True,
            )
        _ensure_remote_defaults_configured_for_learn()
        record = create_learn_session(
            session_name=session_name,
            dataset_repo=dataset,
            config_path=config,
        )
        created_session_name = record.session_name
        snapshot_push_result, remote_urls = _push_snapshots_with_progress(
            workspace=Path("."),
            session_id=None,
            message_id=None,
            push_all=True,
            create_remote=True,
        )
        output_console.print("Syncing Hugging Face dataset...")
        hf_result = push_opencode_exports_to_hf(
            name=record.dataset_repo,
            private=False,
            snapshot_push_result=snapshot_push_result,
        )
        initial_run = create_initial_learn_run(
            session=record,
            config_path=config,
        )
        started_prime_run = start_prime_rl_run(
            prime_config=initial_run.prime_config,
            config_path=config,
        )
        initial_run = record_prime_run_id_for_learn_run(
            run=initial_run,
            prime_run_id=started_prime_run.run_id,
        )
    except RolloutsError as error:
        if created_session_name is not None:
            delete_learn_session_by_name(session_name=created_session_name)
        error_console.print(f"[red]Error:[/red] {error}")
        raise typer.Exit(code=1) from error

    output_console.print("[green]Created learn session[/green]")
    output_console.print(f"session: {record.session_name}")
    output_console.print(f"dataset: {record.dataset_repo}")
    output_console.print(f"config path: {config}")
    output_console.print(f"initial run: {initial_run.run_number}")
    output_console.print(f"prime run id: {initial_run.prime_run_id}")
    output_console.print("")
    output_console.print("[green]Synced dataset[/green]")
    batch_label = hf_result.batch_id if hf_result.batch_id is not None else "none"
    output_console.print(f"repo: {hf_result.repo_id}")
    output_console.print(f"pushed snapshots: {hf_result.pushed_snapshots}")
    output_console.print(f"skipped snapshots: {hf_result.skipped_snapshots}")
    output_console.print(f"created remotes: {hf_result.created_remotes}")
    output_console.print(f"batch: {batch_label}")
    output_console.print(f"added sessions: {hf_result.added_sessions}")
    output_console.print(f"updated sessions: {hf_result.updated_sessions}")
    output_console.print(f"total rows: {hf_result.total_rows}")
    if remote_urls:
        output_console.print("")
        output_console.print("GitHub repos")
        for remote_url in remote_urls:
            output_console.print(f"[link={remote_url}]{remote_url}[/link]")
        output_console.print("")
    output_console.print(f"[link={hf_result.repo_url}]{hf_result.repo_url}[/link]")
    output_console.print("")
    output_console.print(
        f"[link={started_prime_run.dashboard_url}]{started_prime_run.dashboard_url}[/link]"
    )


@learn_app.command("list")
def learn_list() -> None:
    """List global learn sessions."""

    session_statuses = list_learn_session_statuses()
    if not session_statuses:
        output_console.print("No learn sessions found.")
        return

    table = Table()
    table.add_column(f"Session ({len(session_statuses)})", style="green", no_wrap=False)
    table.add_column("Dataset", style="cyan", no_wrap=False)
    table.add_column("Runs", justify="right")
    table.add_column("Latest", justify="right")
    table.add_column("Prime Run ID (click to open)", style="magenta", no_wrap=False)
    table.add_column("Status", style="yellow", no_wrap=False)
    table.add_column("Updated", style="dim")

    for session_status in session_statuses:
        latest_run = session_status.latest_run
        prime_status = "-"
        prime_run_id_cell = (
            latest_run.prime_run_id if latest_run is not None and latest_run.prime_run_id else "-"
        )
        if latest_run is not None and latest_run.prime_run_id is not None:
            try:
                prime_run_status = get_prime_rl_run_status(run_id=latest_run.prime_run_id)
                prime_status = prime_run_status.status
                prime_run_id_cell = (
                    f"[link={prime_run_status.dashboard_url}]{latest_run.prime_run_id}[/link]"
                )
            except RolloutsError:
                prime_status = "ERROR"

        updated_at = (
            latest_run.updated_at if latest_run is not None else session_status.session.updated_at
        )
        table.add_row(
            session_status.session.session_name,
            session_status.session.dataset_repo,
            str(session_status.run_count),
            f"#{latest_run.run_number}" if latest_run is not None else "-",
            prime_run_id_cell,
            prime_status,
            _format_datetime(updated_at),
        )

    output_console.print(table)


@learn_app.command("status")
def learn_status(
    session_name: str = typer.Argument(..., help="Global learn session name."),
) -> None:
    """Show detailed status for a global learn session."""

    try:
        session_status = get_learn_session_status(session_name=session_name)
    except RolloutsError as error:
        error_console.print(f"[red]Error:[/red] {error}")
        raise typer.Exit(code=1) from error

    latest_run = session_status.latest_run
    prime_status = None
    prime_log_lines: list[str] = []
    prime_logs_error: str | None = None
    if latest_run is not None and latest_run.prime_run_id is not None:
        try:
            prime_status = get_prime_rl_run_status(run_id=latest_run.prime_run_id)
        except RolloutsError as error:
            error_console.print(f"[red]Error:[/red] {error}")
            raise typer.Exit(code=1) from error
        if prime_status.error_message or prime_status.status == "FAILED":
            try:
                prime_log_lines = get_prime_rl_run_logs(run_id=latest_run.prime_run_id)
            except RolloutsError as error:
                prime_logs_error = str(error)

    prime_run_id = (
        latest_run.prime_run_id if latest_run is not None and latest_run.prime_run_id else "-"
    )
    prime_checkpoint_id = (
        latest_run.prime_checkpoint_id
        if latest_run is not None and latest_run.prime_checkpoint_id
        else "-"
    )
    prime_model_id = (
        latest_run.prime_model_id if latest_run is not None and latest_run.prime_model_id else "-"
    )
    config_path = (
        str(latest_run.config_path) if latest_run is not None and latest_run.config_path else "-"
    )
    restarted_from_run_id = (
        latest_run.restarted_from_run_id
        if latest_run is not None and latest_run.restarted_from_run_id
        else "-"
    )
    created_at = (
        latest_run.created_at if latest_run is not None else session_status.session.created_at
    )
    updated_at = (
        latest_run.updated_at if latest_run is not None else session_status.session.updated_at
    )

    output_console.print(f"Session: {session_status.session.session_name}")
    output_console.print(f"Dataset: {session_status.session.dataset_repo}")
    output_console.print(f"Runs: {session_status.run_count}")
    output_console.print("")
    output_console.print(
        f"Latest run: #{latest_run.run_number}" if latest_run is not None else "Latest run: -"
    )
    output_console.print(f"Prime run id: {prime_run_id}")
    output_console.print(
        f"Prime status: {prime_status.status if prime_status is not None else '-'}"
    )
    output_console.print(f"Created: {_format_datetime(created_at)}")
    output_console.print(f"Updated: {_format_datetime(updated_at)}")
    output_console.print(f"Prime checkpoint id: {prime_checkpoint_id}")
    output_console.print(f"Prime model id: {prime_model_id}")
    output_console.print(f"Config path: {config_path}")
    output_console.print(f"Restarted from run id: {restarted_from_run_id}")
    if prime_status is not None:
        output_console.print("")
        output_console.print("Dashboard:")
        output_console.print(
            f"[link={prime_status.dashboard_url}]{prime_status.dashboard_url}[/link]"
        )
    if prime_log_lines or prime_logs_error:
        output_console.print("")
        output_console.print("Prime error details:")
        if prime_log_lines:
            for line in prime_log_lines:
                output_console.print(line)
        elif prime_logs_error is not None:
            output_console.print(prime_logs_error)
    if prime_status is not None and prime_status.error_message:
        output_console.print("")
        output_console.print(f"Prime error: {prime_status.error_message}")


@learn_app.command("restart")
def learn_restart(
    session_name: str = typer.Argument(..., help="Global learn session name."),
    config: Path | None = typer.Option(
        None,
        "--config",
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        resolve_path=True,
        help=(
            "Optional Prime config TOML override. Defaults to the latest run's stored config path."
        ),
    ),
) -> None:
    """Restart the latest failed or manually stopped learn run in the same session."""

    try:
        session_status = get_learn_session_status(session_name=session_name)
        latest_run = session_status.latest_run
        if latest_run is None:
            raise RolloutsError(
                f"learn session has no runs: {session_status.session.session_name!r}"
            )
        if latest_run.prime_run_id is None:
            raise RolloutsError(
                f"latest learn run #{latest_run.run_number} does not have a Prime run id recorded"
            )

        source_prime_status = get_prime_rl_run_status(run_id=latest_run.prime_run_id)
        if not _is_restartable_prime_status(source_prime_status.status):
            raise RolloutsError(
                "latest learn run is not restartable; "
                f"Prime status is {source_prime_status.status!r}"
            )

        restart_prime_config, restart_config_path = resolve_learn_run_restart_inputs(
            session=session_status.session,
            source_run=latest_run,
            config_path=config,
        )

        output_console.print("[yellow]Restart confirmation[/yellow]")
        output_console.print(f"session: {session_status.session.session_name}")
        output_console.print(f"dataset: {session_status.session.dataset_repo}")
        output_console.print(f"source run: #{latest_run.run_number}")
        output_console.print(f"source run id: {latest_run.id}")
        output_console.print(f"source Prime run id: {latest_run.prime_run_id}")
        output_console.print(f"source Prime status: {source_prime_status.status}")
        output_console.print(f"new run: #{latest_run.run_number + 1}")
        output_console.print(f"config path: {restart_config_path}")
        output_console.print("")
        output_console.print(
            Panel(
                Syntax(restart_prime_config, "toml", word_wrap=True),
                title="Prime config for restarted run",
                border_style="cyan",
                expand=False,
            )
        )
        output_console.print("")
        confirmed = typer.confirm("Start this restarted learn run?", default=False)
        if not confirmed:
            output_console.print("Cancelled.")
            raise typer.Exit(code=1)

        refreshed_status = get_learn_session_status(session_name=session_name)
        refreshed_latest_run = refreshed_status.latest_run
        if refreshed_latest_run is None or refreshed_latest_run.id != latest_run.id:
            raise RolloutsError(
                "latest learn run changed while waiting for confirmation; run restart again"
            )
        if refreshed_latest_run.prime_run_id is None:
            raise RolloutsError(
                f"latest learn run #{refreshed_latest_run.run_number} no longer has a Prime run id"
            )

        refreshed_prime_status = get_prime_rl_run_status(run_id=refreshed_latest_run.prime_run_id)
        if not _is_restartable_prime_status(refreshed_prime_status.status):
            raise RolloutsError(
                "latest learn run is no longer restartable; "
                f"Prime status is {refreshed_prime_status.status!r}"
            )

        started_prime_run = start_prime_rl_run(
            prime_config=restart_prime_config,
            config_path=restart_config_path,
        )
        restarted_run = create_restarted_learn_run(
            session=refreshed_status.session,
            source_run=refreshed_latest_run,
            prime_config=restart_prime_config,
            config_path=restart_config_path,
            prime_run_id=started_prime_run.run_id,
        )
    except RolloutsError as error:
        error_console.print(f"[red]Error:[/red] {error}")
        raise typer.Exit(code=1) from error

    output_console.print("[green]Restarted learn run[/green]")
    output_console.print(f"session: {session_status.session.session_name}")
    output_console.print(f"source run: #{latest_run.run_number}")
    output_console.print(f"new run: #{restarted_run.run_number}")
    output_console.print(f"config path: {restart_config_path}")
    output_console.print(f"prime run id: {restarted_run.prime_run_id}")
    output_console.print("")
    output_console.print(
        f"[link={started_prime_run.dashboard_url}]{started_prime_run.dashboard_url}[/link]"
    )


@app.command("list")
def list_command(
    workspace: Path | None = typer.Argument(
        None,
        exists=True,
        file_okay=False,
        dir_okay=True,
        readable=True,
        resolve_path=True,
        help="Path to filter by workspace.",
    ),
) -> None:
    """List workspaces and sessions with snapshot counts."""
    try:
        sessions = list_all_sessions()
    except Exception:
        sessions = []

    workspaces = list_all_workspaces()

    if workspace is not None:
        workspace = workspace.resolve()
        sessions = [s for s in sessions if s.workspace_root_path == workspace]

    table = Table()
    table.add_column(f"Workspace ({len(workspaces)})", style="cyan", no_wrap=False)
    table.add_column(f"Session ({len(sessions)})", style="green", no_wrap=False)
    table.add_column("Snapshots", justify="right")
    table.add_column("First", style="dim")
    table.add_column("Last", style="dim")

    for session in sessions:
        table.add_row(
            str(session.workspace_root_path),
            session.session_id,
            str(session.snapshot_count),
            session.first_captured_at.strftime("%Y-%m-%d %H:%M"),
            session.last_captured_at.strftime("%Y-%m-%d %H:%M"),
        )

    output_console.print(table)


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
        help="GitHub repository URL to use as the workspace remote.",
    ),
) -> None:
    """Set the GitHub repo for a workspace."""

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
    """Clear stored GitHub repo remotes without deleting snapshots."""

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
    owner: str | None = typer.Option(
        None,
        "--owner",
        help="GitHub user or organization that should own auto-created GitHub repos.",
    ),
    prefix: str | None = typer.Option(
        None,
        "--prefix",
        help="Prefix to use when deriving auto-created GitHub repo names.",
    ),
    visibility: str | None = typer.Option(
        None,
        "--visibility",
        help="GitHub repo visibility for auto-created GitHub repos: private, public, or internal.",
    ),
) -> None:
    """Set global defaults for auto-created GitHub repos."""

    try:
        _configure_remote_defaults(
            owner=owner,
            prefix="" if prefix is None else prefix,
            visibility="" if visibility is None else visibility,
            prompt_for_missing=True,
        )
    except RolloutsError as error:
        error_console.print(f"[red]Error:[/red] {error}")
        raise typer.Exit(code=1) from error


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
        help="GitHub repo URL or GitHub repo page URL.",
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
        help="Create and remember a GitHub repo when a workspace has no configured remote.",
    ),
) -> None:
    """Push stored snapshots to configured GitHub repos."""

    try:
        validate_push_args(
            session_id=session_id,
            message_id=message_id,
            push_all=push_all,
            create_remote=create_remote,
        )
        result, remote_urls = _push_snapshots_with_progress(
            workspace=workspace,
            session_id=session_id,
            message_id=message_id,
            push_all=push_all,
            create_remote=create_remote,
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
    session_id: str | None = typer.Option(
        None,
        "--session",
        help="Session identifier. Required unless --all is set.",
    ),
    export_all: bool = typer.Option(
        False,
        "--all",
        help="Export all Rollouts-tracked sessions as JSONL.",
    ),
    output_path: Path = typer.Option(
        ...,
        "--out",
        resolve_path=True,
        help="Output JSON or JSONL file path.",
    ),
) -> None:
    """Export agent session data for downstream use."""

    if agent != "opencode":
        error_console.print(f"[red]Error:[/red] unsupported agent: {agent}")
        raise typer.Exit(code=1)
    if export_all and session_id is not None:
        error_console.print("[red]Error:[/red] cannot use --session with --all")
        raise typer.Exit(code=1)
    if not export_all and session_id is None:
        error_console.print("[red]Error:[/red] --session is required unless --all is set")
        raise typer.Exit(code=1)

    try:
        if export_all:
            result = export_opencode_sessions_jsonl(output_path=output_path)
            output_console.print(f"[green]Exported sessions[/green] {result.session_count}")
            output_console.print("format: jsonl")
            output_console.print(f"output: {result.output_path}")
            return

        if session_id is None:
            raise RuntimeError("session_id should be present when --all is not set")
        result = export_opencode_session(session_id=session_id, output_path=output_path)
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


@hf_app.command("push")
def hf_push(
    agent: str = typer.Option(
        ...,
        "--agent",
        help="Agent source to upload. Currently only `opencode` is supported.",
    ),
    name: str = typer.Option(
        ...,
        "--name",
        help=(
            "Dataset repo name or repo id. "
            "If no namespace is provided, your authenticated HF username is used."
        ),
    ),
    private: bool = typer.Option(
        False,
        "--private/--public",
        help="Create the dataset as private or public if it does not already exist.",
    ),
) -> None:
    """Upload tracked session exports to a Hugging Face dataset."""

    if agent != "opencode":
        error_console.print(f"[red]Error:[/red] unsupported agent: {agent}")
        raise typer.Exit(code=1)

    try:
        snapshot_push_result, remote_urls = _push_snapshots_with_progress(
            workspace=Path("."),
            session_id=None,
            message_id=None,
            push_all=True,
            create_remote=True,
        )
        output_console.print("Syncing Hugging Face dataset...")
        result = push_opencode_exports_to_hf(
            name=name,
            private=private,
            snapshot_push_result=snapshot_push_result,
        )
    except RolloutsError as error:
        error_console.print(f"[red]Error:[/red] {error}")
        raise typer.Exit(code=1) from error

    output_console.print("[green]Synced dataset[/green]")
    output_console.print(f"repo: {result.repo_id}")
    output_console.print(f"pushed snapshots: {result.pushed_snapshots}")
    output_console.print(f"skipped snapshots: {result.skipped_snapshots}")
    output_console.print(f"created remotes: {result.created_remotes}")
    output_console.print(f"batch: {result.batch_id if result.batch_id is not None else 'none'}")
    output_console.print(f"added sessions: {result.added_sessions}")
    output_console.print(f"updated sessions: {result.updated_sessions}")
    output_console.print(f"total rows: {result.total_rows}")
    if remote_urls:
        output_console.print("")
        output_console.print("GitHub repos")
        for remote_url in remote_urls:
            output_console.print(f"[link={remote_url}]{remote_url}[/link]")
        output_console.print("")
    output_console.print(f"[link={result.repo_url}]{result.repo_url}[/link]")


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


def _prompt_setup_scope() -> str:
    output_console.print(
        Panel.fit(
            "\n".join(
                [
                    "[bold]Choose where to install the OpenCode Rollouts plugin[/bold]",
                    "",
                    "[cyan]1.[/cyan] [bold]Global[/bold]   [dim]~/.config/opencode/plugins[/dim]",
                    "[cyan]2.[/cyan] [bold]Project[/bold]  [dim].opencode/plugins[/dim]",
                ]
            ),
            title="Rollouts Setup",
            border_style="blue",
            padding=(1, 2),
        )
    )
    selected_scope = typer.prompt(
        "Select an option",
        default="1",
        show_default=True,
    ).strip()

    if selected_scope == "1":
        return "global"
    if selected_scope == "2":
        return "project"
    raise RolloutsError("invalid setup selection; choose 1 for global or 2 for project")


def _ensure_remote_defaults_configured_for_setup() -> RemoteDefaultsRecord:
    return _ensure_remote_defaults_configured(
        message=(
            "GitHub repo defaults are not configured yet. "
            "Set them now so setup leaves learn ready to use."
        ),
        default_prefix="rollouts-",
        default_visibility="private",
    )


def _ensure_remote_defaults_configured_for_learn() -> RemoteDefaultsRecord:
    return _ensure_remote_defaults_configured(
        message="GitHub repo defaults are required before learn can push workspace snapshots.",
        default_prefix="rollouts-",
        default_visibility="public",
    )


def _ensure_remote_defaults_configured(
    *,
    message: str,
    default_prefix: str,
    default_visibility: str,
) -> RemoteDefaultsRecord:
    paths = get_app_paths()
    ensure_app_home(paths)
    with connect(paths) as connection:
        initialize_db(connection)
        defaults = get_remote_defaults(connection)

    if defaults is not None:
        return defaults

    output_console.print(message)
    return _configure_remote_defaults(
        owner=None,
        prefix=default_prefix,
        visibility=default_visibility,
        prompt_for_missing=True,
    )


def _configure_remote_defaults(
    *,
    owner: str | None,
    prefix: str,
    visibility: str,
    prompt_for_missing: bool = False,
) -> RemoteDefaultsRecord:
    resolved_owner = "" if owner is None else owner.strip()
    resolved_prefix = prefix.strip()
    resolved_visibility = visibility.strip()

    if prompt_for_missing:
        if not resolved_owner:
            resolved_owner = typer.prompt("GitHub owner or organization").strip()
        if not resolved_prefix:
            resolved_prefix = typer.prompt(
                "GitHub repo prefix",
                default="rollouts-",
                show_default=True,
            ).strip()
        if not resolved_visibility:
            resolved_visibility = typer.prompt(
                "GitHub repo visibility",
                default="public",
                show_default=True,
            ).strip()

    defaults = set_global_remote_defaults(
        owner=resolved_owner,
        repo_prefix=resolved_prefix,
        visibility=resolved_visibility,
    )
    output_console.print("[green]Configured remote defaults[/green]")
    output_console.print(f"owner: {defaults.owner}")
    output_console.print(f"prefix: {defaults.repo_prefix}")
    output_console.print(f"visibility: {defaults.visibility}")
    output_console.print("")
    return defaults


def _get_cli_version() -> str:
    project_version = _get_local_project_version()
    if project_version is not None:
        return project_version

    try:
        return package_version("agent-rollouts")
    except PackageNotFoundError:
        return "dev"


def _get_local_project_version() -> str | None:
    for parent in Path(__file__).resolve().parents:
        pyproject_path = parent / "pyproject.toml"
        if not pyproject_path.is_file():
            continue

        try:
            pyproject_data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError):
            return None

        project_data = pyproject_data.get("project")
        if not isinstance(project_data, dict):
            return None
        if project_data.get("name") != "agent-rollouts":
            continue

        version = project_data.get("version")
        return version if isinstance(version, str) and version.strip() else None

    return None


def _format_datetime(value) -> str:
    return value.strftime("%Y-%m-%d %H:%M")


def _is_restartable_prime_status(status: str) -> bool:
    return status.strip().upper() in {
        "FAILED",
        "STOPPED",
        "CANCELLED",
        "CANCELED",
        "ABORTED",
    }


def _push_snapshots_with_progress(
    *,
    workspace: Path,
    session_id: str | None,
    message_id: str | None,
    push_all: bool,
    create_remote: bool,
) -> tuple[PushResult, list[str]]:
    scope_counts = get_push_scope_counts(
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
        snapshot_task_id = progress.add_task(
            "Pushing snapshots",
            total=scope_counts.snapshot_count,
        )
        workspace_task_id = progress.add_task(
            "Pushing workspaces",
            total=scope_counts.workspace_count,
        )

        def on_workspace_pushed(workspace_record: WorkspaceRecord) -> None:
            progress.update(workspace_task_id, advance=1)
            if workspace_record.remote_url is not None:
                remote_url = get_github_repo_web_url(workspace_record.remote_url)
                if remote_url not in seen_remote_urls:
                    seen_remote_urls.add(remote_url)
                    remote_urls.append(remote_url)

        def on_snapshot_pushed(snapshot_record: SnapshotRecord) -> None:
            del snapshot_record
            progress.update(snapshot_task_id, advance=1)

        result = push_snapshots(
            workspace=workspace,
            session_id=session_id,
            message_id=message_id,
            push_all=push_all,
            create_remote=create_remote,
            on_workspace_pushed=on_workspace_pushed,
            on_snapshot_pushed=on_snapshot_pushed,
        )

    return result, remote_urls
