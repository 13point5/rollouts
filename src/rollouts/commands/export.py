from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OpenCodeExportResult:
    output_path: Path
    session_id: str
    title: str
    message_count: int


def export_opencode_session(
    *,
    session_id: str,
    output_path: Path,
) -> OpenCodeExportResult:
    raw_json = subprocess.run(
        ["opencode", "export", session_id],
        check=True,
        capture_output=True,
        text=True,
    ).stdout

    payload = json.loads(raw_json)
    info = payload["info"]
    messages = payload["messages"]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(raw_json, encoding="utf-8")

    return OpenCodeExportResult(
        output_path=output_path,
        session_id=info["id"],
        title=info["title"],
        message_count=len(messages),
    )
