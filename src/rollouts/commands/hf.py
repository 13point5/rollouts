from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from huggingface_hub import HfApi, hf_hub_download
from huggingface_hub.errors import HfHubHTTPError

from rollouts.commands.export import build_tracked_opencode_export_payloads
from rollouts.commands.push import PushResult, push_snapshots
from rollouts.errors import RolloutsError

DATASET_FILENAME = "train.jsonl"
DATASET_CARD_FILENAME = "README.md"


@dataclass(frozen=True)
class HfPushResult:
    repo_id: str
    repo_url: str
    batch_id: int | None
    pushed_snapshots: int
    skipped_snapshots: int
    created_remotes: int
    added_sessions: int
    updated_sessions: int
    total_rows: int


@dataclass(frozen=True)
class ExistingDataset:
    rows: list[dict[str, object]]
    repo_files: set[str]


def push_opencode_exports_to_hf(
    *,
    name: str,
    private: bool,
    snapshot_push_result: PushResult | None = None,
) -> HfPushResult:
    if snapshot_push_result is None:
        snapshot_push_result = push_snapshots(
            workspace=Path("."),
            session_id=None,
            message_id=None,
            push_all=True,
            create_remote=True,
        )

    token = os.environ.get("HF_TOKEN")
    api = HfApi()
    auth_info = _require_hf_authentication(api=api, token=token)
    repo_id = _resolve_dataset_repo_id(name=name, auth_info=auth_info)
    export_payloads = build_tracked_opencode_export_payloads()
    if not export_payloads:
        raise RolloutsError("no tracked sessions found to export")

    try:
        api.create_repo(
            repo_id=repo_id,
            repo_type="dataset",
            private=private,
            exist_ok=True,
            token=token,
        )

        local_rows = [dict(payload.payload) for payload in export_payloads]
        existing_dataset = _load_existing_dataset(api=api, repo_id=repo_id, token=token)
        existing_rows, normalized_existing_rows = _normalize_existing_rows(
            existing_rows=existing_dataset.rows
        )
        merged_rows, batch_id, added_sessions, updated_sessions = _append_changed_rows(
            existing_rows=existing_rows,
            local_rows=local_rows,
        )
        should_upload_rows = normalized_existing_rows or batch_id is not None
        should_upload_card = (
            should_upload_rows or DATASET_CARD_FILENAME not in existing_dataset.repo_files
        )

        if should_upload_rows:
            api.upload_file(
                path_or_fileobj=_encode_jsonl_rows(merged_rows),
                path_in_repo=DATASET_FILENAME,
                repo_id=repo_id,
                repo_type="dataset",
                token=token,
                commit_message="Update rollout session exports",
            )

        if should_upload_card:
            api.upload_file(
                path_or_fileobj=_build_dataset_card(repo_id=repo_id).encode("utf-8"),
                path_in_repo=DATASET_CARD_FILENAME,
                repo_id=repo_id,
                repo_type="dataset",
                token=token,
                commit_message="Update rollout dataset card",
            )
    except HfHubHTTPError as error:
        raise RolloutsError(str(error)) from error

    return HfPushResult(
        repo_id=repo_id,
        repo_url=_dataset_repo_url(repo_id),
        batch_id=batch_id,
        pushed_snapshots=snapshot_push_result.pushed_snapshots,
        skipped_snapshots=snapshot_push_result.skipped_snapshots,
        created_remotes=snapshot_push_result.created_remotes,
        added_sessions=added_sessions,
        updated_sessions=updated_sessions,
        total_rows=len(merged_rows),
    )


def _require_hf_authentication(*, api: HfApi, token: str | None) -> Mapping[str, object]:
    try:
        auth_info = api.whoami(token=token)
    except HfHubHTTPError as error:
        raise RolloutsError(
            "not authenticated with Hugging Face. Run `hf auth login` or set HF_TOKEN."
        ) from error

    return auth_info


def _resolve_dataset_repo_id(*, name: str, auth_info: Mapping[str, object]) -> str:
    normalized_name = name.strip()
    if not normalized_name:
        raise RolloutsError("dataset name cannot be empty")
    if "/" in normalized_name:
        return normalized_name

    username = str(auth_info["name"])
    return f"{username}/{normalized_name}"


def _load_existing_dataset(*, api: HfApi, repo_id: str, token: str | None) -> ExistingDataset:
    repo_files = set(api.list_repo_files(repo_id=repo_id, repo_type="dataset", token=token))
    if DATASET_FILENAME not in repo_files:
        return ExistingDataset(rows=[], repo_files=repo_files)

    local_path = hf_hub_download(
        repo_id=repo_id,
        filename=DATASET_FILENAME,
        repo_type="dataset",
        token=token,
    )
    with open(local_path, encoding="utf-8") as input_file:
        return ExistingDataset(
            rows=_parse_jsonl_rows(input_file.read()),
            repo_files=repo_files,
        )


def _parse_jsonl_rows(raw_text: str) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line_number, line in enumerate(raw_text.splitlines(), start=1):
        stripped_line = line.strip()
        if not stripped_line:
            continue
        try:
            row = json.loads(stripped_line)
        except json.JSONDecodeError as error:
            raise RolloutsError(
                f"existing dataset row on line {line_number} is not valid JSON: {error.msg}"
            ) from error
        if not isinstance(row, dict):
            raise RolloutsError(f"existing dataset row on line {line_number} is not a JSON object")
        rows.append(row)

    return rows


def _normalize_existing_rows(
    *,
    existing_rows: list[dict[str, object]],
) -> tuple[list[dict[str, object]], bool]:
    normalized_rows: list[dict[str, object]] = []
    normalized_any_rows = False
    for row in existing_rows:
        normalized_row = dict(row)
        batch_id = normalized_row.get("batch_id")
        if batch_id is None:
            normalized_row["batch_id"] = 1
            normalized_any_rows = True
        elif isinstance(batch_id, bool) or not isinstance(batch_id, int) or batch_id < 1:
            raise RolloutsError("existing dataset row has an invalid batch_id")
        normalized_rows.append(normalized_row)

    return normalized_rows, normalized_any_rows


def _append_changed_rows(
    *,
    existing_rows: list[dict[str, object]],
    local_rows: list[dict[str, object]],
) -> tuple[list[dict[str, object]], int | None, int, int]:
    latest_rows_by_session: dict[str, dict[str, object]] = {}
    for row in existing_rows:
        session_id = _get_row_session_id(row)
        latest_row = latest_rows_by_session.get(session_id)
        if latest_row is None or _get_row_batch_id(row) > _get_row_batch_id(latest_row):
            latest_rows_by_session[session_id] = row

    new_batch_rows: list[dict[str, object]] = []
    added_sessions = 0
    updated_sessions = 0
    for row in local_rows:
        session_id = _get_row_session_id(row)
        latest_row = latest_rows_by_session.get(session_id)
        if latest_row is None:
            new_batch_rows.append(row)
            added_sessions += 1
            continue
        if _build_row_content_signature(row) == _build_row_content_signature(latest_row):
            continue
        new_batch_rows.append(row)
        updated_sessions += 1

    if not new_batch_rows:
        return existing_rows, None, added_sessions, updated_sessions

    next_batch_id = max((_get_row_batch_id(row) for row in existing_rows), default=0) + 1
    appended_rows: list[dict[str, object]] = list(existing_rows)
    for row in new_batch_rows:
        new_row = dict(row)
        new_row["batch_id"] = next_batch_id
        appended_rows.append(new_row)

    return appended_rows, next_batch_id, added_sessions, updated_sessions


def _get_row_session_id(row: Mapping[str, object]) -> str:
    session_id = row.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise RolloutsError("dataset row is missing a valid session_id")
    return session_id


def _get_row_batch_id(row: Mapping[str, object]) -> int:
    batch_id = row.get("batch_id")
    if isinstance(batch_id, bool) or not isinstance(batch_id, int) or batch_id < 1:
        raise RolloutsError("dataset row is missing a valid batch_id")
    return batch_id


def _build_row_content_signature(row: Mapping[str, object]) -> str:
    comparable_row = {
        "session_id": row.get("session_id"),
        "agent": row.get("agent"),
        "session": row.get("session"),
        "metadata": row.get("metadata"),
    }
    return json.dumps(comparable_row, sort_keys=True, separators=(",", ":"))


def _encode_jsonl_rows(rows: Sequence[Mapping[str, object]]) -> bytes:
    return "".join(f"{json.dumps(row)}\n" for row in rows).encode("utf-8")


def _build_dataset_card(*, repo_id: str) -> str:
    dataset_name = repo_id.rsplit("/", maxsplit=1)[-1]
    return f"""---
configs:
- config_name: default
  default: true
  data_files:
  - split: train
    path: {DATASET_FILENAME}
---

# {dataset_name}

This dataset is generated by `rollouts hf push`.

## Usage

```python
from datasets import load_dataset

dataset = load_dataset("{repo_id}", split="train")
```

## Row Shape

Each row is one exported session with these top-level fields:

- `batch_id`
- `session_id`
- `agent`
- `exported_at`
- `session`
- `metadata`
"""


def _dataset_repo_url(repo_id: str) -> str:
    return f"https://huggingface.co/datasets/{repo_id}"
