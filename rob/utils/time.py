from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_timestamp(value: datetime | None) -> str:
    if value is None:
        return "Unknown"
    return value.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
