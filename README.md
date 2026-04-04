# Rollouts

A CLI for Continual Learning with your own coding agent sessions. Track rollouts and codebase snapshots at every turn. The data can be used for SFT, RL, and Continual Learning. Currently supports OpenCode and uses Prime Intellect for hosted RL training.

## Quick Start

```bash
# Install rollouts
uv tool install agent-rollouts

# Install the OpenCode Rollouts plugin
rollouts setup

# Configure default GitHub archive repo settings
rollouts remote defaults set --owner github-username --prefix rollouts-

# Start a learn session
rollouts learn start my-session --config /path/to/rl.toml

# Inspect learn sessions
rollouts learn list
rollouts learn status my-session

# Restart the latest run in the same session if it failed or was stopped
rollouts learn restart my-session

# Or override the config path when restarting
rollouts learn restart my-session --config /path/to/rl.toml
```

Make sure `opencode`, `git`, `gh`, and `hf` are installed first.

If you plan to use `rollouts learn`, make sure your Prime environment is also configured.

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

### Learn Sessions

- `rollouts setup` installs the OpenCode Rollouts plugin.
- `rollouts remote defaults set` is required before `rollouts learn start` because learn sessions push tracked snapshots and may auto-create archive repos.
- `rollouts learn start <session> --config <path>` creates the learn session, stores the config, syncs the dataset, creates run `#1`, and starts the initial Prime run.
- `rollouts learn list` shows your learn sessions.
- `rollouts learn status <session>` shows the latest run, current Prime state, config path, and restart lineage.
- `rollouts learn restart <session>` restarts the latest failed or manually stopped run in the same session after confirmation.
- `rollouts learn restart <session> --config <path>` overrides the stored config path for the restarted run.

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
