"""Shared serialization helpers for the public API."""

from __future__ import annotations

from datetime import datetime, timezone


def iso_z(value: datetime) -> str:
    """ISO 8601 in UTC with a trailing ``Z`` (e.g. ``2026-07-10T00:00:00Z``)."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
