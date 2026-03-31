from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def utc_now_isoformat() -> str:
    return utc_now().isoformat(timespec="milliseconds").replace("+00:00", "Z")
