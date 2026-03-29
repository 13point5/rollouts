# rollouts

Python CLI for capturing and restoring agent rollout workspace states.

## Current status

Current prototype:
- `uv`-managed Python CLI
- global installable `rollouts` command
- `rollouts init <workspace>`
- SQLite bootstrap with a `workspaces` table
- one bare Git store per registered workspace

Not built yet:
- `rollouts capture`
- `rollouts list`
- `rollouts restore`

## Install

For local development:

```bash
uv sync --extra dev
```

To install the CLI for use anywhere on your machine:

```bash
uv tool install --python 3.12 --editable .
```

After that, you can run:

```bash
rollouts --help
```

## Usage

Initialize a workspace:

```bash
uv run rollouts init .
```

Or, after `uv tool install`:

```bash
rollouts init .
```

The `init` command:
- accepts one argument: a path inside a Git repository
- resolves the repository root with Git
- creates the app home at `~/.rollouts` or `$ROLLOUTS_HOME`
- creates `rollouts.sqlite`
- creates a bare store for the workspace
- inserts a row into `workspaces`

The current on-disk layout is:

```text
~/.rollouts/
  rollouts.sqlite
  workspaces/
    <workspace_id>/
      store.git/
```

If you want to test without touching your real home directory:

```bash
ROLLOUTS_HOME="$(mktemp -d)" rollouts init .
```

## Schema

Current database schema:

```sql
CREATE TABLE workspaces (
  id TEXT PRIMARY KEY,
  root_path TEXT NOT NULL UNIQUE,
  store_path TEXT NOT NULL,
  created_at TEXT NOT NULL
);
```

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
