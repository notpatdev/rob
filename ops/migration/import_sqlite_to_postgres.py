from __future__ import annotations

import argparse
import asyncio
import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import asyncpg

from ops.migration.inspect_sqlite import TABLES_OF_INTEREST, inspect_sqlite


KNOWN_TEST_USERNAMES = {"marie_123"}
_COUNT_KEY_RE = re.compile(
    r"^(?:count|counting):(?P<guild>\d+):(?P<field>channel_id|current_number|last_user_id|is_enabled|pending_restore)$",
    re.IGNORECASE,
)
_INACTIVITY_KEY_RE = re.compile(
    r"^inactivity:(?P<guild>\d+):(?P<user>\d+):(?P<field>assigned_at|remove_at|initial_notice_sent|final_notice_sent)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DerivedSend:
    guild_id: int
    domme_user_id: int
    sub_user_id: int | None
    sub_name: str | None
    amount_cents: int
    currency: str
    method: str | None
    source: str
    item_name: str | None
    item_image_url: str | None
    external_id: str | None
    event_id: str | None
    fallback_event_hash: str | None
    is_private: bool
    is_test_send: bool
    sent_at: datetime
    received_at: datetime
    seeded: bool = True
    discord_post_status: str = "posted"


def _safe_db_label(database_url: str) -> str:
    parsed = urlsplit(database_url)
    user = parsed.username or "(user)"
    host = parsed.hostname or "(host)"
    db = parsed.path.lstrip("/") or "(database)"
    return f"{user}@{host}/{db}"


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _fetch_rows(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    if not _table_exists(connection, table):
        return []
    rows = connection.execute(f"SELECT * FROM {table}").fetchall()
    return [dict(row) for row in rows]


def _row_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
        lower_key = key.lower()
        for existing_key, value in row.items():
            if existing_key.lower() == lower_key and value is not None:
                return value
    return None


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        try:
            return int(float(text))
        except ValueError:
            return None


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    return text in {"1", "true", "t", "yes", "y", "on"}


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_throne_handle(value: Any) -> str | None:
    text = _as_text(value)
    if text is None:
        return None
    text = text.strip()
    if "://" in text:
        parsed = urlsplit(text)
        path = parsed.path.strip("/")
        if not path:
            return None
        text = path.split("/", 1)[0]
    text = text.lstrip("@").strip().lower()
    return text or None


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if value is None:
        return datetime.now(timezone.utc)
    text = str(value).strip()
    if not text:
        return datetime.now(timezone.utc)
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    maybe_epoch = _as_int(text)
    if maybe_epoch is not None:
        if maybe_epoch > 10_000_000_000:
            maybe_epoch //= 1000
        return datetime.fromtimestamp(maybe_epoch, tz=timezone.utc)
    return datetime.now(timezone.utc)


def _amount_cents(row: dict[str, Any]) -> int:
    direct = _as_int(_row_value(row, "amount_cents"))
    if direct is not None:
        return max(0, direct)
    usd = _row_value(row, "amount_usd", "amount", "value")
    if usd is None:
        return 0
    try:
        return max(0, int(round(float(str(usd)) * 100)))
    except ValueError:
        return 0


def _resolve_guild_id(row: dict[str, Any], *, default_guild_id: int) -> int:
    value = _as_int(_row_value(row, "guild_id", "server_id"))
    return value if value is not None else default_guild_id


def _guild_ids_from_bot_setting_key(key: str | None) -> set[int]:
    if not key:
        return set()
    guild_ids: set[int] = set()
    count_match = _COUNT_KEY_RE.match(key)
    if count_match:
        guild_ids.add(int(count_match.group("guild")))
    inactivity_match = _INACTIVITY_KEY_RE.match(key)
    if inactivity_match:
        guild_ids.add(int(inactivity_match.group("guild")))
    return guild_ids


def _discover_source_guild_ids(sqlite_path: Path) -> set[int]:
    guild_ids: set[int] = set()
    with sqlite3.connect(sqlite_path) as sqlite:
        sqlite.row_factory = sqlite3.Row
        for table in TABLES_OF_INTEREST:
            for row in _public_source_rows(sqlite, table):
                explicit_guild_id = _as_int(_row_value(row, "guild_id", "server_id"))
                if explicit_guild_id is not None:
                    guild_ids.add(explicit_guild_id)
                key = _as_text(_row_value(row, "key", "config_key", "name"))
                guild_ids.update(_guild_ids_from_bot_setting_key(key))
    return guild_ids


async def _discover_target_guild_ids(database_url: str) -> set[int]:
    connection = await asyncpg.connect(database_url)
    try:
        rows = await connection.fetch(
            """
            SELECT DISTINCT guild_id
            FROM (
                SELECT guild_id FROM vib_settings
                UNION ALL
                SELECT guild_id FROM bot_users
                UNION ALL
                SELECT guild_id FROM dommes
                UNION ALL
                SELECT guild_id FROM subs
                UNION ALL
                SELECT guild_id FROM sends
                UNION ALL
                SELECT guild_id FROM vib_leaderboard
                UNION ALL
                SELECT guild_id FROM the_count
                UNION ALL
                SELECT guild_id FROM inactive_users
            ) guilds
            WHERE guild_id IS NOT NULL
            ORDER BY guild_id ASC
            """
        )
        return {int(row["guild_id"]) for row in rows}
    finally:
        await connection.close()


async def _resolve_default_guild_id(
    *,
    sqlite_path: Path,
    database_url: str,
    explicit_default_guild_id: int | None,
) -> int:
    if explicit_default_guild_id is not None:
        return explicit_default_guild_id

    source_guild_ids = _discover_source_guild_ids(sqlite_path)
    if len(source_guild_ids) == 1:
        return next(iter(source_guild_ids))
    if len(source_guild_ids) > 1:
        discovered = ", ".join(str(guild_id) for guild_id in sorted(source_guild_ids))
        raise RuntimeError(
            "Could not infer a single guild id from the legacy SQLite database. "
            f"Found multiple candidates: {discovered}. Pass --default-guild-id explicitly."
        )

    target_guild_ids = await _discover_target_guild_ids(database_url)
    if len(target_guild_ids) == 1:
        return next(iter(target_guild_ids))
    if len(target_guild_ids) > 1:
        discovered = ", ".join(str(guild_id) for guild_id in sorted(target_guild_ids))
        raise RuntimeError(
            "Could not infer a single guild id from the target PostgreSQL database. "
            f"Found multiple candidates: {discovered}. Pass --default-guild-id explicitly."
        )

    raise RuntimeError(
        "Could not infer a guild id from the legacy SQLite database or the target PostgreSQL "
        "database. Pass --default-guild-id explicitly."
    )


def _parse_bot_setting_value(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if value is None:
        return {"value": ""}
    if isinstance(value, (bool, int, float)):
        return {"value": value}
    text = str(value).strip()
    if not text:
        return {"value": ""}
    if text.startswith("{") or text.startswith("["):
        try:
            parsed = json.loads(text)
            return {"value": parsed}
        except json.JSONDecodeError:
            pass
    return {"value": text}


def _send_signature(send: DerivedSend) -> tuple[int, int, int | None, int, str]:
    return (
        send.guild_id,
        send.domme_user_id,
        send.sub_user_id,
        send.amount_cents,
        send.sent_at.date().isoformat(),
    )


def _public_source_rows(connection: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    return _fetch_rows(connection, table)


def _sorted_rows_by_id(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def sort_key(row: dict[str, Any]) -> tuple[int, int]:
        row_id = _as_int(_row_value(row, "id"))
        if row_id is None:
            return (1, 0)
        return (0, row_id)

    return sorted(rows, key=sort_key)


def _build_payload(
    *,
    sqlite_path: Path,
    default_guild_id: int,
    include_wishlist_cache: bool,
    exclude_domme_handles: set[str] | None = None,
) -> dict[str, Any]:
    with sqlite3.connect(sqlite_path) as sqlite:
        sqlite.row_factory = sqlite3.Row

        source_rows = {table: _public_source_rows(sqlite, table) for table in TABLES_OF_INTEREST}

    excluded_handles = {handle.lower() for handle in (exclude_domme_handles or set())}

    bot_users: dict[tuple[int, int], dict[str, Any]] = {}
    dommes: dict[tuple[int, int], dict[str, Any]] = {}
    subs: dict[tuple[int, int], dict[str, Any]] = {}
    vib_settings: dict[int, dict[str, Any]] = {}
    bot_settings: dict[str, dict[str, Any]] = {}
    count_state: dict[int, dict[str, Any]] = {}
    inactive_users: dict[tuple[int, int], dict[str, Any]] = {}
    report_counts: dict[str, int] = {
        "throne_creators_total": len(source_rows["throne_creators"]),
        "throne_creators_missing_discord_user_id": 0,
        "throne_creators_without_event_domme": 0,
        "throne_creator_conflicts": 0,
        "event_sends_skipped_missing_domme": 0,
        "send_requests_total": len(source_rows["send_requests"]),
        "send_requests_accepted": 0,
        "send_requests_inserted": 0,
        "send_requests_skipped_duplicate": 0,
        "send_requests_skipped_missing_domme": 0,
        "throne_creators_matched_by_handle": 0,
        "excluded_event_dommes_by_handle": 0,
        "excluded_throne_creators_by_handle": 0,
        "wishlist_rows_ignored": (
            0 if include_wishlist_cache else len(source_rows["throne_wishlist_items"])
        ),
    }
    warnings: list[str] = []

    domme_id_lookup: dict[tuple[int, int], int] = {}
    sub_id_lookup: dict[tuple[int, int], int] = {}
    creator_id_to_user: dict[str, int] = {}
    domme_handle_to_user: dict[tuple[int, str], int] = {}

    def ensure_user(guild_id: int, user_id: int, *, blocked: bool = False) -> None:
        key = (guild_id, user_id)
        existing = bot_users.get(key)
        if existing is None:
            existing = {
                "guild_id": guild_id,
                "discord_user_id": user_id,
                "discord_username": None,
                "discord_display_name": None,
                "status": "blocked" if blocked else "allowed",
            }
            bot_users[key] = existing
        elif blocked:
            existing["status"] = "blocked"

    for row in source_rows["event_dommes"]:
        guild_id = _resolve_guild_id(row, default_guild_id=default_guild_id)
        throne_url = _as_text(_row_value(row, "throne_url", "profile_url"))
        normalized_handle = _normalize_throne_handle(
            _row_value(row, "throne_handle", "handle", "throne_url", "profile_url")
        )
        if normalized_handle is not None and normalized_handle in excluded_handles:
            report_counts["excluded_event_dommes_by_handle"] += 1
            continue
        discord_user_id = _as_int(
            _row_value(row, "discord_user_id", "user_id", "domme_user_id")
        )
        if discord_user_id is None:
            continue
        ensure_user(guild_id, discord_user_id)
        domme_key = (guild_id, discord_user_id)
        dommes[domme_key] = {
            "guild_id": guild_id,
            "discord_user_id": discord_user_id,
            "throne_url": throne_url,
            "throne_handle": _as_text(_row_value(row, "throne_handle", "handle"))
            or normalized_handle,
            "throne_creator_id": _as_text(_row_value(row, "throne_creator_id", "creator_id")),
            "tracking_status": "active",
            "profile_status": "active",
            "hide_own_purchases": _as_bool(_row_value(row, "hide_own_purchases")),
            "webhook_secret": _as_text(_row_value(row, "webhook_secret")),
            "webhook_secret_hash": _as_text(_row_value(row, "webhook_secret_hash")),
            "webhook_connected_at": _as_datetime(_row_value(row, "webhook_connected_at"))
            if _row_value(row, "webhook_connected_at")
            else None,
            "last_successful_event_at": _as_datetime(_row_value(row, "last_successful_event_at"))
            if _row_value(row, "last_successful_event_at")
            else None,
            "public_display_name": _as_text(_row_value(row, "public_display_name")),
            "public_display_name_updated_at": _as_datetime(
                _row_value(row, "public_display_name_updated_at")
            )
            if _row_value(row, "public_display_name_updated_at")
            else None,
            "registered_at": _as_datetime(_row_value(row, "registered_at", "created_at")),
        }
        source_id = _as_int(_row_value(row, "id"))
        if source_id is not None:
            domme_id_lookup[(guild_id, source_id)] = discord_user_id
        creator_id = dommes[domme_key]["throne_creator_id"]
        if creator_id:
            creator_id_to_user[creator_id] = discord_user_id
        if normalized_handle:
            domme_handle_to_user[(guild_id, normalized_handle)] = discord_user_id

    for row in _sorted_rows_by_id(source_rows["throne_creators"]):
        guild_id = _resolve_guild_id(row, default_guild_id=default_guild_id)
        incoming_handle = _as_text(_row_value(row, "throne_handle", "handle"))
        normalized_handle = _normalize_throne_handle(incoming_handle)
        if normalized_handle is not None and normalized_handle in excluded_handles:
            report_counts["excluded_throne_creators_by_handle"] += 1
            continue
        discord_user_id = _as_int(
            _row_value(row, "discord_user_id", "user_id", "domme_user_id")
        )
        if discord_user_id is None:
            domme_fk = _as_int(_row_value(row, "domme_id"))
            if domme_fk is not None:
                discord_user_id = domme_id_lookup.get((guild_id, domme_fk))
        if discord_user_id is None:
            report_counts["throne_creators_missing_discord_user_id"] += 1
            continue
        matched_by_handle_user_id: int | None = None
        if (guild_id, discord_user_id) not in dommes and normalized_handle is not None:
            matched_by_handle_user_id = domme_handle_to_user.get((guild_id, normalized_handle))
            if matched_by_handle_user_id is not None:
                report_counts["throne_creators_matched_by_handle"] += 1
                warnings.append(
                    f"Matched throne_creators handle {normalized_handle} in guild {guild_id} "
                    f"to existing Dom/me user {matched_by_handle_user_id} instead of creator row user {discord_user_id}."
                )
                discord_user_id = matched_by_handle_user_id
        if (guild_id, discord_user_id) not in dommes:
            report_counts["throne_creators_without_event_domme"] += 1
        ensure_user(guild_id, discord_user_id)
        domme_key = (guild_id, discord_user_id)
        base = dommes.get(
            domme_key,
            {
                "guild_id": guild_id,
                "discord_user_id": discord_user_id,
                "tracking_status": "active",
                "profile_status": "active",
                "hide_own_purchases": None,
                "public_display_name": None,
                "public_display_name_updated_at": None,
                "registered_at": datetime.now(timezone.utc),
            },
        )
        creator_id = _as_text(_row_value(row, "throne_creator_id", "creator_id"))
        existing_creator_id = _as_text(base.get("throne_creator_id"))
        existing_handle = _as_text(base.get("throne_handle"))
        incoming_handle = _as_text(_row_value(row, "throne_handle", "handle"))

        if creator_id and creator_id in creator_id_to_user and creator_id_to_user[creator_id] != discord_user_id:
            report_counts["throne_creator_conflicts"] += 1
            warnings.append(
                f"Creator ID {creator_id} mapped to multiple users "
                f"({creator_id_to_user[creator_id]} and {discord_user_id}) in guild {guild_id}; keeping first mapping."
            )
        elif creator_id:
            creator_id_to_user[creator_id] = discord_user_id

        if existing_creator_id and creator_id and existing_creator_id != creator_id:
            report_counts["throne_creator_conflicts"] += 1
            warnings.append(
                f"Dom/me {discord_user_id} in guild {guild_id} has multiple creator IDs "
                f"({existing_creator_id}, {creator_id}); keeping {existing_creator_id}."
            )
            creator_id = existing_creator_id

        if existing_handle and incoming_handle and existing_handle.lower() != incoming_handle.lower():
            report_counts["throne_creator_conflicts"] += 1
            warnings.append(
                f"Dom/me {discord_user_id} in guild {guild_id} has multiple handles "
                f"({existing_handle}, {incoming_handle}); keeping {existing_handle}."
            )
            incoming_handle = existing_handle

        base.update(
            {
                "throne_handle": incoming_handle or base.get("throne_handle"),
                "throne_creator_id": creator_id or base.get("throne_creator_id"),
                "hide_own_purchases": _as_bool(_row_value(row, "hide_own_purchases"))
                if _row_value(row, "hide_own_purchases") is not None
                else base.get("hide_own_purchases"),
                "webhook_secret": _as_text(_row_value(row, "webhook_secret"))
                or base.get("webhook_secret"),
                "webhook_secret_hash": _as_text(_row_value(row, "webhook_secret_hash"))
                or base.get("webhook_secret_hash"),
                "webhook_connected_at": _as_datetime(_row_value(row, "webhook_connected_at"))
                if _row_value(row, "webhook_connected_at")
                else base.get("webhook_connected_at"),
                "last_successful_event_at": _as_datetime(
                    _row_value(row, "last_successful_event_at")
                )
                if _row_value(row, "last_successful_event_at")
                else base.get("last_successful_event_at"),
            }
        )
        dommes[domme_key] = base
        resolved_handle = _normalize_throne_handle(
            base.get("throne_handle") or base.get("throne_url")
        )
        if resolved_handle:
            domme_handle_to_user[(guild_id, resolved_handle)] = discord_user_id

    for row in source_rows["event_subs"]:
        guild_id = _resolve_guild_id(row, default_guild_id=default_guild_id)
        discord_user_id = _as_int(_row_value(row, "discord_user_id", "user_id", "sub_user_id"))
        if discord_user_id is None:
            continue
        ensure_user(guild_id, discord_user_id)
        send_name = _as_text(_row_value(row, "send_name", "name", "gifter_name"))
        if send_name is None:
            send_name = f"user-{discord_user_id}"
        sub_key = (guild_id, discord_user_id)
        subs[sub_key] = {
            "guild_id": guild_id,
            "discord_user_id": discord_user_id,
            "send_name": send_name,
            "profile_status": "active",
            "registered_at": _as_datetime(_row_value(row, "registered_at", "created_at")),
        }
        source_id = _as_int(_row_value(row, "id"))
        if source_id is not None:
            sub_id_lookup[(guild_id, source_id)] = discord_user_id

    for row in source_rows["rob_blacklist"]:
        guild_id = _resolve_guild_id(row, default_guild_id=default_guild_id)
        discord_user_id = _as_int(_row_value(row, "discord_user_id", "user_id"))
        if discord_user_id is None:
            continue
        ensure_user(guild_id, discord_user_id, blocked=True)

    for row in source_rows["bot_config"]:
        key = _as_text(_row_value(row, "key", "config_key", "name"))
        if not key:
            continue
        value = _row_value(row, "value", "config_value", "raw_value")
        guild_id = _resolve_guild_id(row, default_guild_id=default_guild_id)

        bot_setting_key = key
        if bot_setting_key in bot_settings and bot_settings[bot_setting_key].get("_guild") != guild_id:
            bot_setting_key = f"{key}:{guild_id}"
        bot_settings[bot_setting_key] = {
            "key": bot_setting_key,
            "value": _parse_bot_setting_value(value),
            "_guild": guild_id,
        }

        if key in {
            "registration_channel_id",
            "leaderboard_channel_id",
            "send_track_channel_id",
            "counting_channel_id",
            "report_channel_id",
            "warn_log_channel_id",
            "domme_role_id",
            "sub_role_id",
            "mod_role_id",
            "inactive_role_id",
            "carlbot_user_id",
        }:
            guild_settings = vib_settings.setdefault(guild_id, {"guild_id": guild_id})
            guild_settings[key] = _as_int(value)

        count_match = _COUNT_KEY_RE.match(key)
        if count_match:
            count_guild_id = int(count_match.group("guild"))
            field = count_match.group("field").lower()
            state = count_state.setdefault(
                count_guild_id,
                {
                    "guild_id": count_guild_id,
                    "channel_id": None,
                    "current_number": 0,
                    "last_user_id": None,
                    "is_enabled": False,
                    "pending_restore": False,
                },
            )
            if field in {"channel_id", "current_number", "last_user_id"}:
                state[field] = _as_int(value)
            else:
                state[field] = _as_bool(value)

        inactivity_match = _INACTIVITY_KEY_RE.match(key)
        if inactivity_match:
            inactive_guild_id = int(inactivity_match.group("guild"))
            inactive_user_id = int(inactivity_match.group("user"))
            field = inactivity_match.group("field")
            ensure_user(inactive_guild_id, inactive_user_id)
            state = inactive_users.setdefault(
                (inactive_guild_id, inactive_user_id),
                {
                    "guild_id": inactive_guild_id,
                    "discord_user_id": inactive_user_id,
                    "inactive_role_assigned_at": None,
                    "remove_at": None,
                    "initial_notice_sent": False,
                    "final_notice_sent": False,
                    "status": "watching",
                },
            )
            if field == "assigned_at":
                state["inactive_role_assigned_at"] = _as_datetime(value)
            elif field == "remove_at":
                state["remove_at"] = _as_datetime(value)
            elif field == "initial_notice_sent":
                state["initial_notice_sent"] = _as_bool(value)
            elif field == "final_notice_sent":
                state["final_notice_sent"] = _as_bool(value)

    sends: list[DerivedSend] = []
    import_signatures: set[tuple[int, int, int | None, int, str]] = set()

    for row in source_rows["event_sends"]:
        guild_id = _resolve_guild_id(row, default_guild_id=default_guild_id)
        domme_user_id = _as_int(
            _row_value(
                row,
                "domme_user_id",
                "recipient_user_id",
                "domme_discord_user_id",
            )
        )
        domme_fk = _as_int(_row_value(row, "domme_id"))
        if domme_user_id is None and domme_fk is not None:
            domme_user_id = domme_id_lookup.get((guild_id, domme_fk))
        creator_id = _as_text(_row_value(row, "throne_creator_id", "creator_id"))
        if domme_user_id is None and creator_id is not None:
            domme_user_id = creator_id_to_user.get(creator_id)
        if domme_user_id is None:
            report_counts["event_sends_skipped_missing_domme"] += 1
            continue

        ensure_user(guild_id, domme_user_id)

        sub_user_id = _as_int(_row_value(row, "sub_user_id", "sender_user_id"))
        sub_fk = _as_int(_row_value(row, "sub_id"))
        if sub_user_id is None and sub_fk is not None:
            sub_user_id = sub_id_lookup.get((guild_id, sub_fk))
        if sub_user_id is not None:
            ensure_user(guild_id, sub_user_id)

        sub_name = _as_text(
            _row_value(row, "sub_name", "sender_name", "gifter_name", "name")
        )
        currency = _as_text(_row_value(row, "currency", "currency_code")) or "USD"
        source = _as_text(_row_value(row, "source")) or "legacy_sqlite_import"
        event_id = _as_text(_row_value(row, "event_id"))
        sent_at = _as_datetime(_row_value(row, "sent_at", "created_at", "received_at"))
        received_at = _as_datetime(_row_value(row, "received_at", "created_at", "sent_at"))

        send = DerivedSend(
            guild_id=guild_id,
            domme_user_id=domme_user_id,
            sub_user_id=sub_user_id,
            sub_name=sub_name,
            amount_cents=_amount_cents(row),
            currency=currency,
            method=_as_text(_row_value(row, "method", "service", "payment_method")),
            source=source,
            item_name=_as_text(
                _row_value(
                    row,
                    "item_name",
                    "item_title",
                    "gift_name",
                    "wishlist_item_name",
                )
            ),
            item_image_url=_as_text(
                _row_value(
                    row,
                    "item_image_url",
                    "item_thumbnail_url",
                    "image_url",
                )
            ),
            external_id=_as_text(_row_value(row, "external_id", "order_id")),
            event_id=event_id,
            fallback_event_hash=_as_text(_row_value(row, "fallback_event_hash")),
            is_private=_as_bool(_row_value(row, "is_private", "private")),
            is_test_send=bool(sub_name and sub_name.strip().lower() in KNOWN_TEST_USERNAMES),
            sent_at=sent_at,
            received_at=received_at,
        )
        sends.append(send)
        import_signatures.add(_send_signature(send))

    for row in source_rows["send_requests"]:
        status = (_as_text(_row_value(row, "status")) or "").lower()
        if status not in {"accepted", "approved", "resolved"}:
            continue
        report_counts["send_requests_accepted"] += 1
        guild_id = _resolve_guild_id(row, default_guild_id=default_guild_id)
        domme_user_id = _as_int(_row_value(row, "domme_user_id", "recipient_user_id"))
        if domme_user_id is None:
            report_counts["send_requests_skipped_missing_domme"] += 1
            continue
        ensure_user(guild_id, domme_user_id)
        sub_user_id = _as_int(_row_value(row, "sub_user_id"))
        if sub_user_id is not None:
            ensure_user(guild_id, sub_user_id)

        candidate = DerivedSend(
            guild_id=guild_id,
            domme_user_id=domme_user_id,
            sub_user_id=sub_user_id,
            sub_name=_as_text(_row_value(row, "sub_name")),
            amount_cents=_amount_cents(row),
            currency=_as_text(_row_value(row, "currency")) or "USD",
            method=_as_text(_row_value(row, "method", "service")),
            source="legacy_send_request",
            item_name=_as_text(_row_value(row, "item_name", "note")),
            item_image_url=None,
            external_id=None,
            event_id=f"legacy_send_request:{_as_int(_row_value(row, 'id')) or 0}",
            fallback_event_hash=None,
            is_private=False,
            is_test_send=False,
            sent_at=_as_datetime(_row_value(row, "resolved_at", "created_at")),
            received_at=_as_datetime(_row_value(row, "resolved_at", "created_at")),
        )
        signature = _send_signature(candidate)
        if signature in import_signatures:
            report_counts["send_requests_skipped_duplicate"] += 1
            continue
        sends.append(candidate)
        import_signatures.add(signature)
        report_counts["send_requests_inserted"] += 1

    vib_leaderboard_rows: list[dict[str, Any]] = []
    for row in source_rows["event_messages"]:
        guild_id = _resolve_guild_id(row, default_guild_id=default_guild_id)
        message_key = _as_text(_row_value(row, "message_key", "key", "event_key"))
        if not message_key:
            message_key = "leaderboard"
        vib_leaderboard_rows.append(
            {
                "guild_id": guild_id,
                "leaderboard_key": message_key,
                "leaderboard_type": "discord",
                "title": _as_text(_row_value(row, "title")) or "Send Leaderboard",
                "channel_id": _as_int(_row_value(row, "channel_id")),
                "message_id": _as_int(_row_value(row, "message_id")),
            }
        )

    if include_wishlist_cache:
        _ = source_rows["throne_wishlist_items"]

    return {
        "source_counts": {table: len(rows) for table, rows in source_rows.items()},
        "bot_users": list(bot_users.values()),
        "dommes": list(dommes.values()),
        "subs": list(subs.values()),
        "sends": sends,
        "vib_settings": list(vib_settings.values()),
        "vib_leaderboard": vib_leaderboard_rows,
        "bot_settings": [
            {"key": entry["key"], "value": entry["value"]}
            for entry in bot_settings.values()
        ],
        "the_count": list(count_state.values()),
        "inactive_users": list(inactive_users.values()),
        "report_counts": report_counts,
        "warnings": warnings,
    }


def _print_report(report: dict[str, Any]) -> None:
    print("SQLite -> PostgreSQL migration report")
    print(f"- source_sqlite: {report['source_sqlite']}")
    print(f"- target_database: {report['target_database']}")
    print(f"- resolved_default_guild_id: {report['resolved_default_guild_id']}")
    print(f"- dry_run: {report['dry_run']}")
    print(f"- inspect_only: {report['inspect_only']}")
    for table_name, count in report["source_counts"].items():
        print(f"- source.{table_name}: {count}")
    for key in (
        "bot_users",
        "dommes",
        "subs",
        "sends",
        "vib_settings",
        "vib_leaderboard",
        "bot_settings",
        "the_count",
        "inactive_users",
    ):
        print(f"- target.{key}: {report['target_counts'][key]}")
    print(f"- send_total_usd_source: {report['event_sends_total_usd']:.2f}")
    print(f"- send_total_usd_target: {report['sends_total_usd']:.2f}")
    import_counts = report["import_counts"]
    print(
        "- send_requests: "
        f"found={import_counts['send_requests_total']}, "
        f"accepted={import_counts['send_requests_accepted']}, "
        f"inserted_into_sends={import_counts['send_requests_inserted']}, "
        f"skipped_duplicate={import_counts['send_requests_skipped_duplicate']}, "
        f"skipped_missing_domme={import_counts['send_requests_skipped_missing_domme']}"
    )
    print(
        "- throne_creators_merge: "
        f"total={import_counts['throne_creators_total']}, "
        f"without_event_domme={import_counts['throne_creators_without_event_domme']}, "
        f"missing_discord_user_id={import_counts['throne_creators_missing_discord_user_id']}, "
        f"conflicts={import_counts['throne_creator_conflicts']}"
    )
    if (
        import_counts.get("throne_creators_matched_by_handle")
        or import_counts.get("excluded_event_dommes_by_handle")
        or import_counts.get("excluded_throne_creators_by_handle")
    ):
        print(
            "- domme_merge_adjustments: "
            f"matched_by_handle={import_counts.get('throne_creators_matched_by_handle', 0)}, "
            f"excluded_event_dommes={import_counts.get('excluded_event_dommes_by_handle', 0)}, "
            f"excluded_throne_creators={import_counts.get('excluded_throne_creators_by_handle', 0)}"
        )
    print(
        "- event_sends_skipped_missing_domme: "
        f"{import_counts['event_sends_skipped_missing_domme']}"
    )
    print(f"- throne_wishlist_items_ignored: {import_counts['wishlist_rows_ignored']}")
    warnings = report.get("warnings", [])
    if warnings:
        print("- warnings:")
        for warning in warnings[:20]:
            print(f"  - {warning}")


def _should_block_prod_truncate(database_url: str) -> bool:
    db_name = urlsplit(database_url).path.lstrip("/").lower()
    return "prod" in db_name


async def _write_payload_to_postgres(
    *,
    database_url: str,
    payload: dict[str, Any],
    truncate_target: bool,
) -> dict[str, int]:
    connection = await asyncpg.connect(database_url)
    try:
        async with connection.transaction():
            if truncate_target:
                await connection.execute(
                    """
                    TRUNCATE TABLE
                        inactive_users,
                        the_count,
                        vib_leaderboard,
                        sends,
                        subs,
                        dommes,
                        bot_users,
                        bot_settings,
                        vib_settings
                    RESTART IDENTITY CASCADE
                    """
                )

            for row in payload["vib_settings"]:
                await connection.execute(
                    """
                    INSERT INTO vib_settings (
                        guild_id,
                        registration_channel_id,
                        leaderboard_channel_id,
                        send_track_channel_id,
                        counting_channel_id,
                        report_channel_id,
                        warn_log_channel_id,
                        domme_role_id,
                        sub_role_id,
                        mod_role_id,
                        inactive_role_id,
                        carlbot_user_id
                    )
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)
                    ON CONFLICT (guild_id) DO UPDATE SET
                        registration_channel_id = EXCLUDED.registration_channel_id,
                        leaderboard_channel_id = EXCLUDED.leaderboard_channel_id,
                        send_track_channel_id = EXCLUDED.send_track_channel_id,
                        counting_channel_id = EXCLUDED.counting_channel_id,
                        report_channel_id = EXCLUDED.report_channel_id,
                        warn_log_channel_id = EXCLUDED.warn_log_channel_id,
                        domme_role_id = EXCLUDED.domme_role_id,
                        sub_role_id = EXCLUDED.sub_role_id,
                        mod_role_id = EXCLUDED.mod_role_id,
                        inactive_role_id = EXCLUDED.inactive_role_id,
                        carlbot_user_id = EXCLUDED.carlbot_user_id,
                        updated_at = now()
                    """,
                    row["guild_id"],
                    row.get("registration_channel_id"),
                    row.get("leaderboard_channel_id"),
                    row.get("send_track_channel_id"),
                    row.get("counting_channel_id"),
                    row.get("report_channel_id"),
                    row.get("warn_log_channel_id"),
                    row.get("domme_role_id"),
                    row.get("sub_role_id"),
                    row.get("mod_role_id"),
                    row.get("inactive_role_id"),
                    row.get("carlbot_user_id"),
                )

            for row in payload["bot_settings"]:
                await connection.execute(
                    """
                    INSERT INTO bot_settings (key, value)
                    VALUES ($1, $2::jsonb)
                    ON CONFLICT (key) DO UPDATE SET
                        value = EXCLUDED.value,
                        updated_at = now()
                    """,
                    row["key"],
                    json.dumps(row["value"]),
                )

            bot_user_ids: dict[tuple[int, int], int] = {}
            for row in payload["bot_users"]:
                inserted = await connection.fetchrow(
                    """
                    INSERT INTO bot_users (
                        guild_id,
                        discord_user_id,
                        discord_username,
                        discord_display_name,
                        status
                    )
                    VALUES ($1, $2, $3, $4, $5)
                    ON CONFLICT (guild_id, discord_user_id) DO UPDATE SET
                        discord_username = COALESCE(EXCLUDED.discord_username, bot_users.discord_username),
                        discord_display_name = COALESCE(EXCLUDED.discord_display_name, bot_users.discord_display_name),
                        status = EXCLUDED.status,
                        updated_at = now()
                    RETURNING id
                    """,
                    row["guild_id"],
                    row["discord_user_id"],
                    row.get("discord_username"),
                    row.get("discord_display_name"),
                    row.get("status", "allowed"),
                )
                assert inserted is not None
                bot_user_ids[(row["guild_id"], row["discord_user_id"])] = int(inserted["id"])

            domme_ids: dict[tuple[int, int], int] = {}
            for row in payload["dommes"]:
                bot_user_id = bot_user_ids.get((row["guild_id"], row["discord_user_id"]))
                inserted = await connection.fetchrow(
                    """
                    INSERT INTO dommes (
                        guild_id,
                        bot_user_id,
                        discord_user_id,
                        throne_url,
                        throne_handle,
                        throne_creator_id,
                        tracking_status,
                        profile_status,
                        hide_own_purchases,
                        webhook_secret,
                        webhook_secret_hash,
                        webhook_connected_at,
                        last_successful_event_at,
                        public_display_name,
                        public_display_name_updated_at,
                        registered_at
                    )
                    VALUES (
                        $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16
                    )
                    ON CONFLICT (guild_id, discord_user_id) DO UPDATE SET
                        bot_user_id = COALESCE(EXCLUDED.bot_user_id, dommes.bot_user_id),
                        throne_url = COALESCE(EXCLUDED.throne_url, dommes.throne_url),
                        throne_handle = COALESCE(EXCLUDED.throne_handle, dommes.throne_handle),
                        throne_creator_id = COALESCE(EXCLUDED.throne_creator_id, dommes.throne_creator_id),
                        tracking_status = EXCLUDED.tracking_status,
                        profile_status = EXCLUDED.profile_status,
                        hide_own_purchases = COALESCE(EXCLUDED.hide_own_purchases, dommes.hide_own_purchases),
                        webhook_secret = COALESCE(EXCLUDED.webhook_secret, dommes.webhook_secret),
                        webhook_secret_hash = COALESCE(EXCLUDED.webhook_secret_hash, dommes.webhook_secret_hash),
                        webhook_connected_at = COALESCE(EXCLUDED.webhook_connected_at, dommes.webhook_connected_at),
                        last_successful_event_at = COALESCE(EXCLUDED.last_successful_event_at, dommes.last_successful_event_at),
                        public_display_name = COALESCE(EXCLUDED.public_display_name, dommes.public_display_name),
                        public_display_name_updated_at = COALESCE(EXCLUDED.public_display_name_updated_at, dommes.public_display_name_updated_at),
                        updated_at = now()
                    RETURNING id
                    """,
                    row["guild_id"],
                    bot_user_id,
                    row["discord_user_id"],
                    row.get("throne_url"),
                    row.get("throne_handle"),
                    row.get("throne_creator_id"),
                    row.get("tracking_status", "active"),
                    row.get("profile_status", "active"),
                    row.get("hide_own_purchases"),
                    row.get("webhook_secret"),
                    row.get("webhook_secret_hash"),
                    row.get("webhook_connected_at"),
                    row.get("last_successful_event_at"),
                    row.get("public_display_name"),
                    row.get("public_display_name_updated_at"),
                    row.get("registered_at"),
                )
                assert inserted is not None
                domme_ids[(row["guild_id"], row["discord_user_id"])] = int(inserted["id"])

            sub_ids: dict[tuple[int, int], int] = {}
            for row in payload["subs"]:
                bot_user_id = bot_user_ids.get((row["guild_id"], row["discord_user_id"]))
                inserted = await connection.fetchrow(
                    """
                    INSERT INTO subs (
                        guild_id,
                        bot_user_id,
                        discord_user_id,
                        send_name,
                        profile_status,
                        registered_at
                    )
                    VALUES ($1,$2,$3,$4,$5,$6)
                    ON CONFLICT (guild_id, discord_user_id) DO UPDATE SET
                        bot_user_id = COALESCE(EXCLUDED.bot_user_id, subs.bot_user_id),
                        send_name = EXCLUDED.send_name,
                        profile_status = EXCLUDED.profile_status,
                        updated_at = now()
                    RETURNING id
                    """,
                    row["guild_id"],
                    bot_user_id,
                    row["discord_user_id"],
                    row["send_name"],
                    row.get("profile_status", "active"),
                    row.get("registered_at"),
                )
                assert inserted is not None
                sub_ids[(row["guild_id"], row["discord_user_id"])] = int(inserted["id"])

            for row in payload["the_count"]:
                await connection.execute(
                    """
                    INSERT INTO the_count (
                        guild_id,
                        channel_id,
                        current_number,
                        last_user_id,
                        is_enabled,
                        pending_restore
                    )
                    VALUES ($1,$2,$3,$4,$5,$6)
                    ON CONFLICT (guild_id) DO UPDATE SET
                        channel_id = EXCLUDED.channel_id,
                        current_number = EXCLUDED.current_number,
                        last_user_id = EXCLUDED.last_user_id,
                        is_enabled = EXCLUDED.is_enabled,
                        pending_restore = EXCLUDED.pending_restore,
                        updated_at = now()
                    """,
                    row["guild_id"],
                    row.get("channel_id"),
                    row.get("current_number", 0),
                    row.get("last_user_id"),
                    row.get("is_enabled", False),
                    row.get("pending_restore", False),
                )

            for row in payload["inactive_users"]:
                bot_user_id = bot_user_ids.get((row["guild_id"], row["discord_user_id"]))
                await connection.execute(
                    """
                    INSERT INTO inactive_users (
                        guild_id,
                        bot_user_id,
                        discord_user_id,
                        inactive_role_assigned_at,
                        remove_at,
                        initial_notice_sent,
                        final_notice_sent,
                        status
                    )
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                    ON CONFLICT (guild_id, discord_user_id) DO UPDATE SET
                        bot_user_id = COALESCE(EXCLUDED.bot_user_id, inactive_users.bot_user_id),
                        inactive_role_assigned_at = EXCLUDED.inactive_role_assigned_at,
                        remove_at = EXCLUDED.remove_at,
                        initial_notice_sent = EXCLUDED.initial_notice_sent,
                        final_notice_sent = EXCLUDED.final_notice_sent,
                        status = EXCLUDED.status,
                        updated_at = now()
                    """,
                    row["guild_id"],
                    bot_user_id,
                    row["discord_user_id"],
                    row.get("inactive_role_assigned_at"),
                    row.get("remove_at"),
                    row.get("initial_notice_sent", False),
                    row.get("final_notice_sent", False),
                    row.get("status", "watching"),
                )

            for send in payload["sends"]:
                domme_id = domme_ids.get((send.guild_id, send.domme_user_id))
                sub_id = (
                    sub_ids.get((send.guild_id, send.sub_user_id))
                    if send.sub_user_id is not None
                    else None
                )
                await connection.execute(
                    """
                    INSERT INTO sends (
                        guild_id,
                        domme_id,
                        domme_user_id,
                        sub_id,
                        sub_user_id,
                        sub_name,
                        amount_cents,
                        currency,
                        method,
                        source,
                        item_name,
                        item_image_url,
                        external_id,
                        event_id,
                        fallback_event_hash,
                        is_private,
                        is_test_send,
                        seeded,
                        sent_at,
                        received_at,
                        discord_post_status,
                        discord_posted_at
                    )
                    VALUES (
                        $1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22
                    )
                    ON CONFLICT (event_id) DO NOTHING
                    """,
                    send.guild_id,
                    domme_id,
                    send.domme_user_id,
                    sub_id,
                    send.sub_user_id,
                    send.sub_name,
                    send.amount_cents,
                    send.currency,
                    send.method,
                    send.source,
                    send.item_name,
                    send.item_image_url,
                    send.external_id,
                    send.event_id,
                    send.fallback_event_hash,
                    send.is_private,
                    send.is_test_send,
                    send.seeded,
                    send.sent_at,
                    send.received_at,
                    send.discord_post_status,
                    send.sent_at if send.discord_post_status == "posted" else None,
                )

            for row in payload["vib_leaderboard"]:
                await connection.execute(
                    """
                    INSERT INTO vib_leaderboard (
                        guild_id,
                        leaderboard_key,
                        leaderboard_type,
                        title,
                        channel_id,
                        message_id
                    )
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (guild_id, leaderboard_key) DO UPDATE SET
                        leaderboard_type = EXCLUDED.leaderboard_type,
                        title = EXCLUDED.title,
                        channel_id = EXCLUDED.channel_id,
                        message_id = EXCLUDED.message_id,
                        updated_at = now()
                    """,
                    row["guild_id"],
                    row["leaderboard_key"],
                    row.get("leaderboard_type", "discord"),
                    row.get("title", "Send Leaderboard"),
                    row.get("channel_id"),
                    row.get("message_id"),
                )

        return {
            "bot_users": len(payload["bot_users"]),
            "dommes": len(payload["dommes"]),
            "subs": len(payload["subs"]),
            "sends": len(payload["sends"]),
            "vib_settings": len(payload["vib_settings"]),
            "vib_leaderboard": len(payload["vib_leaderboard"]),
            "bot_settings": len(payload["bot_settings"]),
            "the_count": len(payload["the_count"]),
            "inactive_users": len(payload["inactive_users"]),
        }
    finally:
        await connection.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import legacy Rob SQLite data into the Rob v2 PostgreSQL schema."
    )
    parser.add_argument("--sqlite", required=True, help="Path to legacy SQLite database file.")
    parser.add_argument("--database-url", required=True, help="Target PostgreSQL database URL.")
    parser.add_argument(
        "--default-guild-id",
        type=int,
        default=None,
        help=(
            "Fallback guild id. Optional when the importer can infer a single guild from the "
            "legacy SQLite data or the target PostgreSQL database."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="When enabled (default), do not write to PostgreSQL.",
    )
    parser.add_argument(
        "--inspect-only",
        action="store_true",
        help="Only inspect source SQLite and derived payload, then exit.",
    )
    parser.add_argument(
        "--truncate-target",
        action="store_true",
        help="Truncate target v2 data tables before import (requires --confirm-truncate).",
    )
    parser.add_argument(
        "--confirm-truncate",
        action="store_true",
        help="Required with --truncate-target.",
    )
    parser.add_argument(
        "--allow-prod-truncate",
        action="store_true",
        help="Allow truncate when target DB name contains 'prod'.",
    )
    parser.add_argument(
        "--include-wishlist-cache",
        action="store_true",
        help="Read throne_wishlist_items into report context (not imported by default).",
    )
    parser.add_argument("--report-json", default="", help="Optional JSON report output path.")
    parser.add_argument(
        "--exclude-domme-handle",
        action="append",
        default=[],
        help="Repeat to skip legacy Dom/me rows by Throne handle during import shaping.",
    )
    return parser.parse_args()


async def main_async(args: argparse.Namespace) -> int:
    sqlite_path = Path(args.sqlite)
    if not sqlite_path.exists():
        raise RuntimeError(f"SQLite database not found: {sqlite_path}")

    default_guild_id = await _resolve_default_guild_id(
        sqlite_path=sqlite_path,
        database_url=args.database_url,
        explicit_default_guild_id=(
            int(args.default_guild_id) if args.default_guild_id is not None else None
        ),
    )

    if args.truncate_target and not args.confirm_truncate:
        raise RuntimeError(
            "--truncate-target requires --confirm-truncate."
        )
    if (
        args.truncate_target
        and _should_block_prod_truncate(args.database_url)
        and not args.allow_prod_truncate
    ):
        raise RuntimeError(
            "Refusing to truncate a prod-like database. "
            "Pass --allow-prod-truncate to override intentionally."
        )

    inspection = inspect_sqlite(sqlite_path)
    payload = _build_payload(
        sqlite_path=sqlite_path,
        default_guild_id=default_guild_id,
        include_wishlist_cache=bool(args.include_wishlist_cache),
        exclude_domme_handles={str(handle).strip().lower() for handle in args.exclude_domme_handle},
    )

    sends_total_usd = round(
        sum(send.amount_cents for send in payload["sends"]) / 100.0,
        2,
    )
    report: dict[str, Any] = {
        "source_sqlite": str(sqlite_path),
        "target_database": _safe_db_label(args.database_url),
        "resolved_default_guild_id": default_guild_id,
        "dry_run": bool(args.dry_run),
        "inspect_only": bool(args.inspect_only),
        "source_counts": payload["source_counts"],
        "target_counts": {
            "bot_users": len(payload["bot_users"]),
            "dommes": len(payload["dommes"]),
            "subs": len(payload["subs"]),
            "sends": len(payload["sends"]),
            "vib_settings": len(payload["vib_settings"]),
            "vib_leaderboard": len(payload["vib_leaderboard"]),
            "bot_settings": len(payload["bot_settings"]),
            "the_count": len(payload["the_count"]),
            "inactive_users": len(payload["inactive_users"]),
        },
        "event_sends_total_usd": float(inspection["event_sends_total_usd"]),
        "sends_total_usd": sends_total_usd,
        "import_counts": payload.get("report_counts", {}),
        "warnings": payload.get("warnings", []),
    }

    if not args.inspect_only and not args.dry_run:
        written = await _write_payload_to_postgres(
            database_url=args.database_url,
            payload=payload,
            truncate_target=bool(args.truncate_target),
        )
        report["target_counts"] = written

    _print_report(report)

    if args.report_json:
        report_path = Path(args.report_json)
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        print(f"- report_json_written: {report_path}")

    return 0


def main() -> None:
    args = parse_args()
    raise SystemExit(asyncio.run(main_async(args)))


if __name__ == "__main__":
    main()
