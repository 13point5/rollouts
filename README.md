# Rollouts

A CLI for capturing agent rollouts and codebase snapshots at every turn. This data can be used for SFT, RL, and Continual Learning.

## Current status

Current prototype:

- `uv`-managed Python CLI
- global installable `rollouts` command
- `rollouts snapshot [workspace] --session --message --metadata`
- `rollouts restore [workspace] --session --message --dest`
- `rollouts restore --repo <repo> --session --message --dest`
- `rollouts delete [workspace] [--session] [--message]` and `rollouts delete --all`
- `rollouts export --agent opencode --session <session-id> --out <file>`
- `rollouts remote set [workspace] --url <repo>`
- `rollouts remote clear [workspace]` and `rollouts remote clear --all`
- `rollouts remote defaults set --owner <owner> [--prefix <prefix>]`
- `rollouts push [workspace] [--session] [--message]` and `rollouts push --all`
- SQLite bootstrap with `workspaces`, `snapshots`, and `remote_defaults` tables
- one bare Git store per registered workspace

## Coming soon

The current CLI focuses on snapshot storage, restore, and archive push/pull primitives. The next major piece is first-class rollout tracking from agent runtimes like OpenCode, Codex, Claude Code, and similar coding agents.

We also plan to add commands for uploading collected trajectories directly to Hugging Face datasets for downstream analysis, training, and continual learning workflows.

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

There is no separate `init` command. The first `snapshot` call automatically registers the workspace, creates the app home at `~/.rollouts` or `$ROLLOUTS_HOME`, bootstraps `rollouts.sqlite`, and creates the workspace bare store.

Create a snapshot for a session message:

```bash
rollouts snapshot . \
  --session ses_123 \
  --message msg_001 \
  --metadata '{"timestamp":"2026-03-29T20:02:43.622Z","kind":"hooks","name":"chat.message"}'
```

The `snapshot` command:

- defaults the workspace path to `.`
- requires `--session`
- requires `--message`
- requires `--metadata`
- expects `--metadata` to be an inline JSON string
- automatically initializes the workspace if it has not been registered yet
- uses the Git repository root when the path is inside a Git repo
- otherwise uses the directory path itself as the workspace root
- can update an existing workspace root if the same directory later becomes Git-backed
- snapshots the current workspace state
- if the source is a Git repo, snapshots tracked and untracked non-ignored files
- if the source is a plain directory, snapshots files recursively and excludes `.git`
- stores per-snapshot VCS metadata in the `vcs` column
- excludes the Rollouts app home if it lives inside the source directory
- stores the resulting Git commit in the workspace bare store
- inserts a row into `snapshots`

Restore a snapshot for a session message:

```bash
rollouts restore . \
  --session ses_123 \
  --message msg_001 \
  --dest /tmp/restored
```

The `restore` command:

- takes the source workspace path as its argument, defaulting to `.`
- also accepts `--repo` to restore directly from a remote archive repo instead of a local workspace
- requires `--session`
- requires `--message`
- requires `--dest`
- with no `--repo`, finds the matching local snapshot by `session_id` and `message_id`
- with `--repo`, looks up the remote annotated tag for the given `session_id` and `message_id`
- extracts the stored snapshot into the destination as a plain codebase directory
- works for snapshots created from both Git repos and plain directories
- fails if the destination already exists

Restore a snapshot directly from a remote archive repo:

```bash
rollouts restore \
  --repo https://github.com/13point5/rollouts-opencode-rollouts-plugin-2c1c8861 \
  --session ses_123 \
  --message msg_001 \
  --dest /tmp/restored
```

Configure an archive remote for a workspace:

```bash
rollouts remote set . --url git@github.com:you/my-project-rollouts.git
```

The `remote set` command:

- defaults the workspace path to `.`
- automatically initializes the workspace if it has not been registered yet
- stores one archive repo URL per workspace
- uses the user’s normal local Git credentials when later pushing

Clear stored archive remotes without deleting snapshots:

```bash
rollouts remote clear .
rollouts remote clear --all
```

The `remote clear` command:

- defaults the workspace path to `.`
- with no `--all`, clears the stored `remote_url` for one workspace
- with `--all`, clears stored `remote_url`s for every registered workspace
- does not delete snapshots, workspace records, or remote defaults
- is useful when you deleted archive repos and want `push --create-remote` to recreate them

Configure defaults for auto-created GitHub archive repos:

```bash
rollouts remote defaults set --owner you --prefix rollouts- --visibility private
```

The `remote defaults set` command:

- stores one global owner/prefix/visibility config for repo auto-creation
- requires the GitHub CLI `gh` to be installed
- does not create any repos by itself

Push stored snapshots to archive remotes:

```bash
rollouts push . --session ses_123 --message msg_001
rollouts push . --session ses_123
rollouts push .
rollouts push --all
rollouts push . --create-remote
rollouts push --all --create-remote
```

The `push` command:

- defaults the workspace path to `.`
- with `--session` and `--message`, pushes one snapshot
- with only `--session`, pushes all snapshots for that session in the workspace
- with no `--session`, pushes all snapshots for that workspace
- with `--all`, pushes all snapshots for all workspaces that have a configured remote
- with `--create-remote`, auto-creates and stores a GitHub archive repo for any workspace in scope that does not already have a configured remote
- requires `--session` when `--message` is provided
- does not allow combining `--all` with `--session` or `--message`
- skips snapshots whose remote tag already exists
- stores remote metadata in annotated tags, not just in SQLite
- uses `gh repo create` for repo creation and then normal `git push` for snapshot upload

Delete stored Rollouts data:

```bash
rollouts delete . --session ses_123 --message msg_001
rollouts delete . --session ses_123
rollouts delete .
rollouts delete --all
```

The `delete` command:

- defaults the workspace path to `.`
- asks for confirmation in every mode
- with `--session` and `--message`, deletes one stored snapshot
- with only `--session`, deletes all stored snapshots for that session in the workspace
- with no `--session`, deletes the whole workspace entry, its stored snapshots, and its local bare store
- with `--all`, deletes the entire Rollouts app home, including the SQLite DB and all workspace stores
- requires `--session` when `--message` is provided
- does not allow combining `--all` with `--session` or `--message`

Export one OpenCode session as raw JSON:

```bash
rollouts export \
  --agent opencode \
  --session ses_123 \
  --out /tmp/opencode-session.json
```

The `export` command:

- uses `opencode export <sessionID>` under the hood
- requires the `opencode` CLI to be installed
- currently supports `--agent opencode`
- requires `--session`
- writes the raw JSON returned by `opencode export` unchanged
- keeps the file compatible with `opencode import`

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
ROLLOUTS_HOME="$(mktemp -d)" rollouts snapshot . --session ses_test --message msg_test --metadata '{"event":"test"}'
```

## Schema

Current database schema:

### `workspaces`

| Column       | Type           | Notes                                                                                         |
| ------------ | -------------- | --------------------------------------------------------------------------------------------- |
| `id`         | `TEXT`         | Primary key. Internal workspace id.                                                           |
| `root_path`  | `TEXT`         | Unique resolved root path for the tracked source directory.                                   |
| `store_path` | `TEXT`         | Path to the workspace bare Git store under `~/.rollouts/workspaces/<workspace_id>/store.git`. |
| `remote_url` | `TEXT \| NULL` | Optional archive repo URL used by `rollouts push`.                                            |
| `created_at` | `TEXT`         | UTC ISO 8601 timestamp for workspace registration.                                            |

### `remote_defaults`

| Column        | Type      | Notes                                                       |
| ------------- | --------- | ----------------------------------------------------------- |
| `id`          | `INTEGER` | Fixed to `1`. Single-row defaults table.                    |
| `owner`       | `TEXT`    | GitHub user or organization for auto-created archive repos. |
| `repo_prefix` | `TEXT`    | Prefix used when deriving auto-created archive repo names.  |
| `visibility`  | `TEXT`    | `private`, `public`, or `internal`.                         |

### `snapshots`

| Column             | Type   | Notes                                                                     |
| ------------------ | ------ | ------------------------------------------------------------------------- |
| `id`               | `TEXT` | Primary key. Internal snapshot id.                                        |
| `workspace_id`     | `TEXT` | Foreign key to `workspaces.id`.                                           |
| `session_id`       | `TEXT` | External chat session identifier.                                         |
| `message_id`       | `TEXT` | External message identifier.                                              |
| `store_commit_sha` | `TEXT` | Commit SHA in the workspace bare store for this snapshot.                 |
| `vcs`              | `TEXT` | JSON string with per-snapshot VCS context.                                |
| `metadata`         | `TEXT` | Raw inline metadata JSON string from the hook or caller.                  |
| `captured_at`      | `TEXT` | UTC ISO 8601 timestamp generated by the CLI when the snapshot is created. |

### `snapshots.vcs`

| Field           | Type             | When present | Notes                                                                                                                                                       |
| --------------- | ---------------- | ------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `vcs`           | `string \| null` | Always       | `"git"` for Git-backed snapshots, otherwise `null`.                                                                                                         |
| `worktree_path` | `string`         | Git only     | Resolved top-level path returned by `git rev-parse --show-toplevel`. For linked worktrees, this is the linked worktree root, not the shared common Git dir. |
| `branch`        | `string \| null` | Git only     | Current branch name when `HEAD` is attached. `null` in detached HEAD state.                                                                                 |
| `head_commit`   | `string \| null` | Git only     | Current `HEAD` commit SHA at snapshot time.                                                                                                                 |

### `snapshots.metadata`

| Field                 | Type        | Notes                                                                                                        |
| --------------------- | ----------- | ------------------------------------------------------------------------------------------------------------ |
| varies by integration | JSON object | Stored as-is for later post-processing. Rollouts does not currently enforce a fixed schema for this payload. |

## Remote Tags

Pushed snapshots use annotated tags in the configured archive repo:

```text
refs/tags/rollouts/session/<session_hash>/message/<message_hash>
```

The tag annotation stores:

- `schema_version`
- `snapshot_id`
- raw `session_id`
- raw `message_id`
- `captured_at`
- `store_commit_sha`
- parsed `vcs`
- parsed `metadata`

Auto-created archive repos use a derived name like:

```text
<prefix><workspace-slug>-<workspace-id-prefix>
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
