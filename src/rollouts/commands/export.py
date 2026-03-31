from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from rollouts.errors import RolloutsError
from rollouts.paths import get_app_paths
from rollouts.storage.db import connect, get_workspace_for_session, initialize_db, list_session_ids
from rollouts.utils import utc_now_isoformat


@dataclass(frozen=True)
class OpenCodeExportResult:
    output_path: Path
    session_id: str
    title: str
    message_count: int
    metadata: dict[str, str | None] | None


@dataclass(frozen=True)
class OpenCodeJsonlExportResult:
    output_path: Path
    session_count: int


@dataclass(frozen=True)
class ParsedOpenCodeExport:
    payload: dict[str, object]
    session_id: str
    title: str
    message_count: int


@dataclass(frozen=True)
class OpenCodeExportPayload:
    payload: Mapping[str, object]
    session_id: str
    title: str
    message_count: int
    metadata: dict[str, str | None] | None


def build_opencode_export_payload(*, session_id: str) -> OpenCodeExportPayload:
    raw_json = _run_opencode_export(session_id=session_id)
    parsed = _parse_opencode_export(raw_json)
    metadata = _get_rollouts_metadata(session_id=parsed.session_id)

    payload = {
        "session_id": parsed.session_id,
        "agent": "opencode",
        "exported_at": utc_now_isoformat(),
        "session": parsed.payload,
        "metadata": metadata,
    }

    return OpenCodeExportPayload(
        payload=payload,
        session_id=parsed.session_id,
        title=parsed.title,
        message_count=parsed.message_count,
        metadata=metadata,
    )


def export_opencode_session(
    *,
    session_id: str,
    output_path: Path,
) -> OpenCodeExportResult:
    export_data = build_opencode_export_payload(session_id=session_id)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(f"{json.dumps(export_data.payload, indent=2)}\n", encoding="utf-8")

    return OpenCodeExportResult(
        output_path=output_path,
        session_id=export_data.session_id,
        title=export_data.title,
        message_count=export_data.message_count,
        metadata=export_data.metadata,
    )


def build_tracked_opencode_export_payloads() -> list[OpenCodeExportPayload]:
    return [
        build_opencode_export_payload(session_id=session_id)
        for session_id in _list_tracked_session_ids()
    ]


def export_opencode_sessions_jsonl(*, output_path: Path) -> OpenCodeJsonlExportResult:
    export_payloads = build_tracked_opencode_export_payloads()
    if not export_payloads:
        raise RolloutsError("no tracked sessions found to export")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as output_file:
        for export_data in export_payloads:
            output_file.write(json.dumps(export_data.payload))
            output_file.write("\n")

    return OpenCodeJsonlExportResult(
        output_path=output_path,
        session_count=len(export_payloads),
    )


def _run_opencode_export(*, session_id: str) -> str:
    try:
        return subprocess.run(
            ["opencode", "export", session_id],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except FileNotFoundError as error:
        raise RolloutsError("opencode executable not found") from error
    except subprocess.CalledProcessError as error:
        message = error.stderr.strip() or error.stdout.strip() or "opencode export failed"
        raise RolloutsError(message) from error


def _parse_opencode_export(raw_json: str) -> ParsedOpenCodeExport:
    payload = json.loads(raw_json)

    info = payload["info"]
    messages = payload["messages"]
    parsed_session_id = info["id"]
    title = info["title"]

    return ParsedOpenCodeExport(
        payload=payload,
        session_id=parsed_session_id,
        title=title,
        message_count=len(messages),
    )


def _get_rollouts_metadata(*, session_id: str) -> dict[str, str | None] | None:
    paths = get_app_paths()
    if not paths.db_path.exists():
        return None

    with connect(paths) as connection:
        initialize_db(connection)
        workspace = get_workspace_for_session(connection, session_id=session_id)

    if workspace is None:
        return None

    return {"remote_url": workspace.remote_url}


def _list_tracked_session_ids() -> list[str]:
    paths = get_app_paths()
    if not paths.db_path.exists():
        return []

    with connect(paths) as connection:
        initialize_db(connection)
        return list_session_ids(connection)
