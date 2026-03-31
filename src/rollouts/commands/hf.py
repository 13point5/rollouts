from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass

from huggingface_hub import HfApi, hf_hub_download
from huggingface_hub.errors import HfHubHTTPError

from rollouts.commands.export import build_tracked_opencode_export_payloads
from rollouts.errors import RolloutsError

DATASET_FILENAME = "train.jsonl"
DATASET_CARD_FILENAME = "README.md"


@dataclass(frozen=True)
class HfPushResult:
    repo_id: str
    repo_url: str
    added_sessions: int
    updated_sessions: int
    total_sessions: int


def push_opencode_exports_to_hf(
    *,
    name: str,
    private: bool,
) -> HfPushResult:
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
        existing_rows = _load_existing_rows(api=api, repo_id=repo_id, token=token)
        merged_rows, added_sessions, updated_sessions = _merge_rows(
            existing_rows=existing_rows,
            local_rows=local_rows,
        )

        api.upload_file(
            path_or_fileobj=_encode_jsonl_rows(merged_rows),
            path_in_repo=DATASET_FILENAME,
            repo_id=repo_id,
            repo_type="dataset",
            token=token,
            commit_message="Update rollout session exports",
        )
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
        added_sessions=added_sessions,
        updated_sessions=updated_sessions,
        total_sessions=len(merged_rows),
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


def _load_existing_rows(*, api: HfApi, repo_id: str, token: str | None) -> list[dict[str, object]]:
    repo_files = api.list_repo_files(repo_id=repo_id, repo_type="dataset", token=token)
    if DATASET_FILENAME not in repo_files:
        return []

    local_path = hf_hub_download(
        repo_id=repo_id,
        filename=DATASET_FILENAME,
        repo_type="dataset",
        token=token,
    )
    with open(local_path, encoding="utf-8") as input_file:
        return _parse_jsonl_rows(input_file.read())


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


def _merge_rows(
    *,
    existing_rows: list[dict[str, object]],
    local_rows: list[dict[str, object]],
) -> tuple[list[Mapping[str, object]], int, int]:
    local_rows_by_session: dict[str, dict[str, object]] = {}
    for row in local_rows:
        session_id = _get_row_session_id(row)
        local_rows_by_session[session_id] = row

    merged_rows: list[Mapping[str, object]] = []
    existing_session_ids: set[str] = set()
    updated_sessions = 0
    for row in existing_rows:
        session_id = _get_row_session_id(row)
        if session_id in existing_session_ids:
            raise RolloutsError(f"existing dataset contains duplicate session_id: {session_id}")
        existing_session_ids.add(session_id)

        local_row = local_rows_by_session.get(session_id)
        if local_row is None:
            merged_rows.append(row)
            continue

        merged_rows.append(local_row)
        updated_sessions += 1

    added_sessions = 0
    for session_id, row in local_rows_by_session.items():
        if session_id in existing_session_ids:
            continue
        merged_rows.append(row)
        added_sessions += 1

    return merged_rows, added_sessions, updated_sessions


def _get_row_session_id(row: Mapping[str, object]) -> str:
    session_id = row.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise RolloutsError("dataset row is missing a valid session_id")
    return session_id


def _encode_jsonl_rows(rows: list[Mapping[str, object]]) -> bytes:
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

- `session_id`
- `agent`
- `exported_at`
- `session`
- `metadata`
"""


def _dataset_repo_url(repo_id: str) -> str:
    return f"https://huggingface.co/datasets/{repo_id}"
