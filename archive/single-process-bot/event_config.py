from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord

from bot.ui.theme import (
    ROB_BLUE,
    ROB_GOLD,
    ROB_GREEN,
    ROB_GREY,
    ROB_PINK,
    ROB_PURPLE,
    ROB_RED,
)

log = logging.getLogger(__name__)

_COLOR_MAP: dict[str, discord.Colour] = {
    "blue": ROB_BLUE,
    "gold": ROB_GOLD,
    "green": ROB_GREEN,
    "grey": ROB_GREY,
    "gray": ROB_GREY,
    "pink": ROB_PINK,
    "purple": ROB_PURPLE,
    "red": ROB_RED,
}


@dataclass(frozen=True)
class EventTheme:
    key: str
    name: str
    emoji: str
    accent_color: discord.Colour
    leaderboard_title: str
    send_title: str


@dataclass(frozen=True)
class ConfiguredEvent:
    key: str
    name: str
    theme: EventTheme
    enabled: bool
    start_at: datetime | None
    end_at: datetime | None
    timezone: str

    def is_active(self, now: datetime | None = None) -> bool:
        if not self.enabled or self.start_at is None or self.end_at is None:
            return False
        current = now or datetime.now(timezone.utc)
        return self.start_at <= current < self.end_at

    @property
    def is_config_complete(self) -> bool:
        return self.start_at is not None and self.end_at is not None


@dataclass(frozen=True)
class EventsConfig:
    source_path: Path
    default_theme: EventTheme
    events: tuple[ConfiguredEvent, ...]

    def active_events(self, now: datetime | None = None) -> list[ConfiguredEvent]:
        current = now or datetime.now(timezone.utc)
        return [event for event in self.events if event.is_active(current)]

    def get_event(self, event_key: str) -> ConfiguredEvent | None:
        for event in self.events:
            if event.key == event_key:
                return event
        return None


def load_events_config(path: Path) -> EventsConfig:
    default_theme = EventTheme(
        key="default",
        name="Default",
        emoji="🏆",
        accent_color=ROB_PURPLE,
        leaderboard_title="Leaderboard",
        send_title="New send just dropped",
    )

    if not path.exists():
        log.warning(
            "Events config %s is missing. Rob will use the default live theme only.",
            path,
        )
        return EventsConfig(source_path=path, default_theme=default_theme, events=())

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning(
            "Could not load events config %s: %s. Rob will use the default theme only.",
            path,
            exc,
        )
        return EventsConfig(source_path=path, default_theme=default_theme, events=())

    if not isinstance(payload, dict):
        log.warning(
            "Events config %s is not a JSON object. Rob will use the default theme only.",
            path,
        )
        return EventsConfig(source_path=path, default_theme=default_theme, events=())

    parsed_default = _parse_theme(
        raw=payload.get("default_theme"),
        key="default",
        name="Default",
        fallback=default_theme,
    )

    events: list[ConfiguredEvent] = []
    for raw_event in payload.get("events", []):
        event = _parse_event(raw_event, fallback=parsed_default)
        if event is not None:
            events.append(event)

    return EventsConfig(
        source_path=path,
        default_theme=parsed_default,
        events=tuple(events),
    )


def _parse_event(raw: Any, *, fallback: EventTheme) -> ConfiguredEvent | None:
    if not isinstance(raw, dict):
        log.warning("Skipping malformed event config row: %r", raw)
        return None

    key = str(raw.get("key") or "").strip()
    if not key:
        log.warning("Skipping event config row without a key: %r", raw)
        return None

    name = str(raw.get("name") or key).strip()
    timezone_name = str(raw.get("timezone") or "UTC").strip() or "UTC"
    enabled = bool(raw.get("enabled", False))
    theme = _parse_theme(raw=raw.get("theme"), key=key, name=name, fallback=fallback)
    start_at = _parse_event_datetime(
        raw.get("start_at"),
        timezone_name=timezone_name,
        label=f"{key}.start_at",
    )
    end_at = _parse_event_datetime(
        raw.get("end_at"),
        timezone_name=timezone_name,
        label=f"{key}.end_at",
    )

    if enabled and (start_at is None or end_at is None):
        log.warning(
            "Event %s is enabled but missing start_at or end_at. Rob will keep it inactive.",
            key,
        )
    elif start_at is not None and end_at is not None and end_at <= start_at:
        log.warning(
            "Event %s has end_at earlier than start_at. Rob will keep it inactive.", key
        )
        start_at = None
        end_at = None

    return ConfiguredEvent(
        key=key,
        name=name,
        theme=theme,
        enabled=enabled,
        start_at=start_at,
        end_at=end_at,
        timezone=timezone_name,
    )


def _parse_theme(
    *,
    raw: Any,
    key: str,
    name: str,
    fallback: EventTheme,
) -> EventTheme:
    if not isinstance(raw, dict):
        raw = {}

    emoji = str(raw.get("emoji") or fallback.emoji or "🏆").strip() or "🏆"
    accent_name = str(raw.get("accent_color") or "").strip().casefold()
    accent_color = _COLOR_MAP.get(accent_name, fallback.accent_color)
    if accent_name and accent_name not in _COLOR_MAP:
        log.warning(
            "Unknown accent_color %r for event %s. Falling back to purple.",
            accent_name,
            key,
        )
        accent_color = ROB_PURPLE

    leaderboard_title = str(
        raw.get("leaderboard_title") or name or fallback.leaderboard_title
    ).strip()
    send_title = str(raw.get("send_title") or fallback.send_title).strip()

    return EventTheme(
        key=key,
        name=name,
        emoji=emoji,
        accent_color=accent_color,
        leaderboard_title=leaderboard_title or fallback.leaderboard_title,
        send_title=send_title or fallback.send_title,
    )


def _parse_event_datetime(
    value: Any, *, timezone_name: str, label: str
) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        log.warning(
            "Event config value %s should be a string or null, not %r.", label, value
        )
        return None

    raw = value.strip()
    if not raw:
        return None

    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        log.warning("Event config value %s could not be parsed: %r", label, value)
        return None

    if parsed.tzinfo is None:
        try:
            parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
        except ZoneInfoNotFoundError:
            log.warning(
                "Event config timezone %r for %s is unknown. Rob will treat it as UTC.",
                timezone_name,
                label,
            )
            parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)
