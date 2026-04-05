# Database Schema

The SQLite schema lives in [src/rollouts/storage/db.py](/Users/13point5/projects/rollouts/src/rollouts/storage/db.py).

## `workspaces`

| Column | SQLite type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | `TEXT` | no | Primary key. |
| `root_path` | `TEXT` | no | Unique workspace root path. |
| `store_path` | `TEXT` | no | Path to the local snapshot store. |
| `remote_url` | `TEXT` | yes | Unique Git remote URL when configured. |
| `created_at` | `TEXT` | no | ISO timestamp. |

## `snapshots`

| Column | SQLite type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | `TEXT` | no | Primary key. |
| `workspace_id` | `TEXT` | no | Foreign key to `workspaces.id`. |
| `session_id` | `TEXT` | no | External chat session identifier. |
| `message_id` | `TEXT` | no | External message identifier. |
| `store_commit_sha` | `TEXT` | no | Commit SHA in the snapshot store. |
| `vcs` | `TEXT` | no | Version control system label. |
| `metadata` | `TEXT` | no | JSON metadata payload stored as text. |
| `captured_at` | `TEXT` | no | ISO timestamp. |

Unique constraint:

| Columns |
| --- |
| `workspace_id`, `session_id`, `message_id` |

## `remote_defaults`

| Column | SQLite type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | `INTEGER` | no | Single-row table with `CHECK (id = 1)`. |
| `owner` | `TEXT` | no | Default GitHub owner/org. |
| `repo_prefix` | `TEXT` | no | Prefix used when creating repos. |
| `visibility` | `TEXT` | no | `private`, `public`, or `internal`. |

## `learn_sessions`

| Column | SQLite type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | `TEXT` | no | Primary key. |
| `session_name` | `TEXT` | no | Unique learn-session name. |
| `dataset_repo` | `TEXT` | no | Hugging Face dataset repo id. |
| `prime_config` | `TEXT` | no | Stored Prime RL TOML. |
| `created_at` | `TEXT` | no | ISO timestamp. |
| `updated_at` | `TEXT` | no | ISO timestamp. |

## `learn_runs`

| Column | SQLite type | Nullable | Notes |
| --- | --- | --- | --- |
| `id` | `TEXT` | no | Primary key. |
| `session_id` | `TEXT` | no | Foreign key to `learn_sessions.id`. |
| `run_number` | `INTEGER` | no | Monotonic per-session run number. |
| `type` | `TEXT` | no | Run lineage type: `start`, `restart`, or `continue`. |
| `prime_run_id` | `TEXT` | yes | Prime-hosted run id. Unique when present. |
| `source_checkpoint_id` | `TEXT` | yes | Checkpoint used to start this run. |
| `prime_checkpoint_id` | `TEXT` | yes | Checkpoint produced by this run, if tracked. |
| `prime_model_id` | `TEXT` | yes | Model/artifact id produced by this run, if tracked. |
| `prime_config` | `TEXT` | no | Effective Prime RL TOML used for this run. |
| `config_path` | `TEXT` | yes | Source config file path on disk. |
| `parent_run_id` | `TEXT` | yes | Parent run in the lineage graph. |
| `created_at` | `TEXT` | no | ISO timestamp. |
| `updated_at` | `TEXT` | no | ISO timestamp. |

Unique constraints:

| Columns |
| --- |
| `session_id`, `run_number` |
| `prime_run_id` |

## Indexes And Triggers

| Object | Kind | Definition |
| --- | --- | --- |
| `snapshots_session_id_idx` | index | `snapshots(session_id)` |
| `snapshots_session_workspace_guard` | trigger | Prevents a single external `session_id` from spanning multiple workspaces. |
| `learn_runs_session_id_run_number_idx` | index | `learn_runs(session_id, run_number)` |
| `learn_runs_prime_run_id_idx` | unique index | `learn_runs(prime_run_id)` |

## Migration Notes

| Change | Behavior |
| --- | --- |
| `type` on `learn_runs` | Added by migration for older databases and defaulted to `start` for pre-existing rows. |
| `parent_run_id` on `learn_runs` | Generalized lineage column used by both restart and continue flows. Older `restarted_from_run_id` values are moved into this column during migration. |
