# rollouts

Python CLI for capturing and restoring agent rollout workspace states.

## Current status

Current prototype:
- `uv`-managed Python CLI
- global installable `rollouts` command
- `rollouts init <workspace>`
- `rollouts snapshot [workspace] --session --turn --metadata`
- `rollouts restore <dest> [workspace] --session --turn`
- SQLite bootstrap with a `workspaces` table
- one bare Git store per registered workspace

Not built yet:
- `rollouts list`

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

Create a snapshot for a session turn:

```bash
rollouts snapshot . \
  --session ses_123 \
  --turn turn_001 \
  --metadata '{"timestamp":"2026-03-29T20:02:43.622Z","kind":"hooks","name":"chat.message"}'
```

The `snapshot` command:
- defaults the workspace path to `.`
- requires `--session`
- requires `--turn`
- requires `--metadata`
- expects `--metadata` to be an inline JSON string
- automatically initializes the workspace if it has not been registered yet
- snapshots the current Git-visible workspace state
- stores the resulting Git commit in the workspace bare store
- inserts a row into `snapshots`

Restore the latest snapshot for a session turn:

```bash
rollouts restore . \
  --session ses_123 \
  --turn turn_001 \
  --dest /tmp/restored
```

The `restore` command:
- takes the source workspace path as its argument, defaulting to `.`
- requires `--session`
- requires `--turn`
- requires `--dest`
- finds the latest matching snapshot by `captured_at`
- extracts the stored snapshot into the destination as a plain codebase directory
- fails if the destination already exists

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

CREATE TABLE snapshots (
  id TEXT PRIMARY KEY,
  workspace_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  turn_id TEXT NOT NULL,
  store_commit_sha TEXT NOT NULL,
  metadata TEXT NOT NULL,
  captured_at TEXT NOT NULL,
  FOREIGN KEY (workspace_id) REFERENCES workspaces(id)
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
