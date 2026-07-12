from __future__ import annotations

import hmac
import json
import logging
from typing import Any
from urllib.parse import urlsplit

import discord
from aiohttp import web


log = logging.getLogger(__name__)


GUILD_CHANNEL_FIELDS = (
    "registration_channel_id",
    "leaderboard_channel_id",
    "send_track_channel_id",
    "counting_channel_id",
    "report_channel_id",
    "warn_log_channel_id",
    "backup_approval_channel_id",
)

GUILD_CHANNEL_LABELS = {
    "registration_channel_id": "Registration Channel",
    "leaderboard_channel_id": "Leaderboard Channel",
    "send_track_channel_id": "Send Tracker Channel",
    "counting_channel_id": "Counting Channel",
    "report_channel_id": "Report Channel",
    "warn_log_channel_id": "Warn Log Channel",
    "backup_approval_channel_id": "Backup Approval Channel",
}

GUILD_CHANNEL_MATCH_TOKENS = {
    "registration_channel_id": ("registration", "register", "setup", "welcome"),
    "leaderboard_channel_id": ("leaderboard", "rank", "leader-board"),
    "send_track_channel_id": ("send-tracker", "send-tracking", "sendtracker", "throne", "sends"),
    "counting_channel_id": ("counting", "count"),
    "report_channel_id": ("report", "support", "help"),
    "warn_log_channel_id": ("warn", "warning", "mod-log", "logs", "log"),
    "backup_approval_channel_id": (
        "backup-approval",
        "backup-approvals",
        "server-backup",
        "backup",
        "backups",
        "mod-approval",
        "approvals",
    ),
}

GUILD_ROLE_FIELDS = (
    "domme_role_id",
    "sub_role_id",
    "mod_role_id",
    "inactive_role_id",
    "leaderboard_view_role_id",
    "active_role_id",
    "unverified_role_id",
    "trial_mod_role_id",
)

GUILD_ROLE_LABELS = {
    "domme_role_id": "Dom/me Role",
    "sub_role_id": "Sub Role",
    "mod_role_id": "Moderator Role",
    "inactive_role_id": "Inactive Role",
    "leaderboard_view_role_id": "Leaderboard Access Role",
    "active_role_id": "Active Role",
    "unverified_role_id": "Unverified Role",
    "trial_mod_role_id": "Trial Moderator Role",
}

GUILD_ROLE_MATCH_TOKENS = {
    "domme_role_id": ("domme", "dom/me", "dom", "dommes"),
    "sub_role_id": ("sub", "subs"),
    "mod_role_id": ("mod", "mods", "moderator", "staff", "admin"),
    "inactive_role_id": ("inactive", "inactivity", "away"),
    "leaderboard_view_role_id": (
        "leaderboard access",
        "leaderboard",
        "leader board",
        "board access",
        "vip",
    ),
    "active_role_id": ("active", "active member", "verified member"),
    "unverified_role_id": ("unverified", "unverify", "not-verified", "pending", "newcomer"),
    "trial_mod_role_id": ("trial mod", "trialmod", "trial-mod", "trial moderator", "trial"),
}

SCAN_APPLY_FIELD_ORDER = (*GUILD_CHANNEL_FIELDS, *GUILD_ROLE_FIELDS)
WEBHOOK_REISSUE_SENT_PREFIX = "migration:webhook_reissue"


def _normalize_scan_name(name: str) -> str:
    return name.strip().lower().replace("_", "-").replace(" ", "-")


def _score_named_match(name: str, tokens: tuple[str, ...]) -> int:
    normalized = _normalize_scan_name(name)
    score = 0
    for token in tokens:
        normalized_token = _normalize_scan_name(token)
        if normalized == normalized_token:
            score = max(score, 100)
        elif normalized.startswith(normalized_token):
            score = max(score, 75)
        elif normalized_token in normalized:
            score = max(score, 50)
    return score


def _preview_handle_from_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlsplit(url)
    path = parsed.path.strip("/")
    if not path:
        return None
    handle = path.split("/", 1)[0].strip().lstrip("@")
    return handle or None


def _find_best_channel_match(channels: list[dict[str, Any]], field_name: str) -> dict[str, Any] | None:
    tokens = GUILD_CHANNEL_MATCH_TOKENS[field_name]
    scored: list[tuple[int, dict[str, Any]]] = []
    for channel in channels:
        score = _score_named_match(str(channel["name"]), tokens)
        if score:
            scored.append((score, channel))
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], str(item[1]["name"]).lower(), int(item[1]["id"])))
    return scored[0][1]


def _find_best_role_match(roles: list[dict[str, Any]], field_name: str) -> dict[str, Any] | None:
    tokens = GUILD_ROLE_MATCH_TOKENS[field_name]
    scored: list[tuple[int, dict[str, Any]]] = []
    for role in roles:
        score = _score_named_match(str(role["name"]), tokens)
        if score:
            scored.append((score, role))
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], str(item[1]["name"]).lower(), int(item[1]["id"])))
    return scored[0][1]


class SupportMixin:

    @staticmethod
    def _host_is_loopback(host: str | None) -> bool:
        return (host or "").strip().lower() in {"127.0.0.1", "::1", "localhost"}

    def _is_authorized(self, request: web.Request) -> bool:
        if self.secret:
            provided = request.headers.get("X-Rob-Ops-Secret", "")
            return hmac.compare_digest(provided, self.secret)
        # Fail closed: with no secret configured, only serve on loopback.
        return self._host_is_loopback(self.host)

    @staticmethod
    def _wants_text(request: web.Request) -> bool:
        return request.query.get("format", "").strip().lower() == "text"

    @staticmethod
    def _match_guild_id(request: web.Request) -> int | None:
        try:
            return int(request.match_info["guild_id"])
        except (KeyError, TypeError, ValueError):
            return None

    @staticmethod
    async def _json_payload(request: web.Request) -> dict[str, Any]:
        try:
            payload = await request.json()
        except Exception:
            payload = None
        if isinstance(payload, dict):
            return payload

        try:
            form_payload = await request.post()
        except Exception:
            return {}

        parsed: dict[str, Any] = {}
        for key in form_payload.keys():
            values = form_payload.getall(key)
            if not values:
                continue
            parsed[key] = values if len(values) > 1 else values[0]
        return parsed

    @staticmethod
    def _payload_user_id(payload: dict[str, Any]) -> int | None:
        try:
            return int(payload.get("discord_user_id"))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _payload_bool(payload: dict[str, Any], key: str) -> bool:
        value = payload.get(key)
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _display_text(value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, dict):
            nested = value.get("value")
            if nested is None:
                return None
            text = str(nested).strip()
            return text or None
        text = str(value).strip()
        if not text:
            return None
        if text.startswith("{") and text.endswith("}"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return text
            if isinstance(parsed, dict):
                nested = parsed.get("value")
                if nested is None:
                    return None
                nested_text = str(nested).strip()
                return nested_text or None
        return text

    @staticmethod
    def _payload_send_names(payload: dict[str, Any]) -> list[str]:
        raw = payload.get("send_names")
        if isinstance(raw, list):
            return [str(item).strip() for item in raw if str(item).strip()]
        if raw is None:
            return []
        if isinstance(raw, str):
            parts = [segment.strip() for segment in raw.replace("\n", ",").split(",")]
            return [part for part in parts if part]
        return []

    async def _resolve_domme(self, guild_id: int, lookup: str):
        cleaned = lookup.strip()
        if cleaned.startswith("@"):
            cleaned = cleaned[1:]
        if hasattr(self.bot, "send_change_request_service"):
            return await self.bot.send_change_request_service._resolve_domme(guild_id, cleaned)
        if cleaned.isdigit() and hasattr(self.bot, "dommes_repo"):
            return await self.bot.dommes_repo.get_by_user_id(guild_id, int(cleaned))
        if hasattr(self.bot, "dommes_repo"):
            direct = await self.bot.dommes_repo.get_by_handle(guild_id, cleaned)
            if direct is not None:
                return direct
            for domme in await self.bot.dommes_repo.list_for_guild(guild_id):
                if (domme.public_display_name or "").casefold() == cleaned.casefold():
                    return domme
        return None

    async def _refresh_guild(self, guild_id: int) -> None:
        if not hasattr(self.bot, "leaderboard_service"):
            return
        try:
            await self.bot.leaderboard_service.refresh_guild(guild_id)
        except Exception:
            log.exception("Guild refresh failed after bot ops mutation guild_id=%s", guild_id)

    async def _resolve_display_name(self, guild_id: int, discord_user_id: int) -> str | None:
        guild = self.bot.get_guild(guild_id)
        if guild is not None:
            member = guild.get_member(discord_user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(discord_user_id)
                except (discord.NotFound, discord.HTTPException, AttributeError):
                    member = None
            if member is not None:
                return (member.display_name or member.name or "").strip() or None
        user = self.bot.get_user(discord_user_id) if hasattr(self.bot, "get_user") else None
        if user is not None:
            return (getattr(user, "display_name", None) or getattr(user, "name", "")).strip() or None
        return None
