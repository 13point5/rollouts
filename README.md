# Rollouts

A CLI for Continual Learning with your own coding agent sessions. Track rollouts and codebase snapshots at every turn. The data can be used for SFT, RL, and Continual Learning. Currently supports OpenCode and uses Prime Intellect for hosted RL training.

## Quick Start

```bash
# Install rollouts
uv tool install agent-rollouts

# Install the OpenCode Rollouts plugin and configure GitHub repo defaults if not already set.
rollouts setup

# Start a learn session
rollouts learn start my-session --config /path/to/rl.toml

# Continue from the latest run, reusing its config and the latest available checkpoint
rollouts learn continue my-session

# Continue from a specific run with a different config file
rollouts learn continue my-session --from-run 2 --config /path/to/another-rl.toml

# Deploy the latest usable adapter for the latest run and use it in global OpenCode config
rollouts learn use my-session

# Restart the latest run in the same session if it failed or was stopped
rollouts learn restart my-session

# Or override the config path when restarting
rollouts learn restart my-session --config /path/to/rl.toml

# Inspect learn sessions
rollouts learn list
rollouts learn status my-session
```

Make sure `prime`, `opencode`, `git`, `gh`, and `hf` are installed and configured first.

## Installation

### Using uv

Install from PyPI:

```bash
uv tool install agent-rollouts
```

Upgrade later:

```bash
uv tool upgrade agent-rollouts
```

## Usage

### Core

```bash
# Show the installed Rollouts version.
rollouts --version

# Install the OpenCode Rollouts plugin globally or for one project.
rollouts setup [WORKSPACE] [--scope global|project]

# Store a workspace snapshot for one external session message.
rollouts snapshot [WORKSPACE] --session <session_id> --message <message_id> --metadata <json>

# List tracked workspaces and sessions.
rollouts list [WORKSPACE]

# Restore a stored snapshot into a destination directory.
rollouts restore [WORKSPACE] [--repo <github_repo_url>] --session <session_id> --message <message_id> --dest <path>

# Push stored snapshots to configured GitHub repos.
rollouts push [WORKSPACE] [--session <session_id>] [--message <message_id>] [--all] [--create-remote]

# Export tracked agent sessions for downstream use.
rollouts export --agent opencode [--session <session_id> | --all] --out <path>

# Delete local Rollouts data.
rollouts delete [WORKSPACE] [--session <session_id>] [--message <message_id>] [--all]
```

### Remote

```bash
# Set the GitHub remote for a tracked workspace.
rollouts remote set [WORKSPACE] --url <github_repo_url>

# Clear one workspace remote or all stored remotes.
rollouts remote clear [WORKSPACE] [--all]

# Set defaults for auto-created GitHub repos.
rollouts remote defaults set [--owner <github_owner>] [--prefix <repo_prefix>] [--visibility private|public|internal]
```

### Hugging Face

```bash
# Sync tracked OpenCode exports to a Hugging Face dataset.
rollouts hf push --agent opencode --name <dataset_name_or_repo_id> [--private|--public]
```

### Learn

```bash
# Start a new continual-learning session and initial Prime run.
rollouts learn start <session_name> [--dataset <dataset_name|repo_id|url>] --config <path>

# List all learn sessions.
rollouts learn list

# Show detailed status for one learn session.
rollouts learn status <session_name>

# Restart the latest failed or manually stopped run.
rollouts learn restart <session_name> [--config <path>]

# Continue from an earlier run, optionally from a checkpoint.
rollouts learn continue <session_name> [--from-run <run_number|latest>] [--config <path>] [--checkpoint <latest|none|checkpoint_id>]

# Deploy a run's adapter if needed and make OpenCode use it globally.
rollouts learn use <session_name> [--run <run_number|latest>]
```

If an older run was created before config-path tracking existed, `rollouts learn restart` may require `--config` the first time you retry it.

## Local Repo Setup

For local development:

```bash
uv sync --extra dev
```

To install the current checkout as a tool:

```bash
uv tool install --editable .
```

You usually do not need to pass `--python` here as long as `uv` selects a compatible interpreter for the project.

## Development

Quality checks:

```bash
uv run ruff check .
uv run ty check src
```

Pre-commit hooks are configured for:

- `ruff --fix`
- `ruff format`
- `ty check src/`

Install the Git hook locally with:

```bash
uv run pre-commit install
```

Run hooks manually with:

```bash
uv run pre-commit run --all-files
```
