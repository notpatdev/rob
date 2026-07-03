from __future__ import annotations

import hmac
import json
import logging
from typing import Any
from datetime import datetime, timezone
from urllib.parse import urlsplit

from aiohttp import web
import discord
from rob.utils.money import dollars_to_cents, format_money_from_cents

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


class BotOpsServer:
    def __init__(
        self,
        *,
        bot: discord.Client,
        host: str,
        port: int,
        secret: str | None = None,
    ) -> None:
        self.bot = bot
        self.host = host
        self.port = port
        self.secret = secret
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    async def start(self) -> None:
        if self._runner is not None:
            return

        app = web.Application()
        app.router.add_get("/health", self._handle_health)
        app.router.add_get("/maintenance", self._handle_get_maintenance)
        app.router.add_get("/rob-offline", self._handle_get_rob_offline)
        app.router.add_get("/guilds/{guild_id}/scan", self._handle_guild_scan)
        app.router.add_get("/guilds/{guild_id}/count", self._handle_get_count)
        app.router.add_get("/guilds/{guild_id}/migration/audit", self._handle_migration_audit)
        app.router.add_get(
            "/guilds/{guild_id}/webhook/reissue/preview",
            self._handle_webhook_reissue_preview,
        )
        app.router.add_post(
            "/guilds/{guild_id}/leaderboard/public/refresh-names",
            self._handle_refresh_public_names,
        )
        app.router.add_post(
            "/guilds/{guild_id}/leaderboard/refresh",
            self._handle_refresh_leaderboard,
        )
        app.router.add_post("/ops/sends/process", self._handle_process_send)
        app.router.add_post("/sends/process", self._handle_process_send)
        app.router.add_post("/maintenance", self._handle_set_maintenance)
        app.router.add_post("/rob-offline", self._handle_set_rob_offline)
        app.router.add_post("/guilds/{guild_id}/count", self._handle_set_count)
        app.router.add_get("/guilds/{guild_id}/inactivity", self._handle_get_inactivity)
        app.router.add_post("/guilds/{guild_id}/inactivity", self._handle_set_inactivity)
        app.router.add_post("/guilds/{guild_id}/inactivity/backfill", self._handle_inactivity_backfill)
        app.router.add_get("/guilds/{guild_id}/backup", self._handle_get_backup)
        app.router.add_post("/guilds/{guild_id}/backup", self._handle_set_backup)
        app.router.add_post("/guilds/{guild_id}/backup/run", self._handle_backup_run)
        app.router.add_post("/guilds/{guild_id}/settings/channel", self._handle_set_guild_channel)
        app.router.add_post("/guilds/{guild_id}/settings/role", self._handle_set_guild_role)
        app.router.add_post("/guilds/{guild_id}/scan/apply", self._handle_apply_guild_scan)
        app.router.add_post(
            "/guilds/{guild_id}/webhook/reissue/send",
            self._handle_webhook_reissue_send,
        )
        app.router.add_post(
            "/guilds/{guild_id}/webhook/reissue/refresh",
            self._handle_webhook_reissue_refresh,
        )
        app.router.add_post("/guilds/{guild_id}/dommes", self._handle_add_domme)
        app.router.add_post("/guilds/{guild_id}/dommes/remove", self._handle_remove_domme)
        app.router.add_post("/guilds/{guild_id}/subs", self._handle_add_sub)
        app.router.add_post("/guilds/{guild_id}/subs/remove", self._handle_remove_sub)
        app.router.add_post("/guilds/{guild_id}/send-requests/add", self._handle_request_send_add)
        app.router.add_post(
            "/guilds/{guild_id}/send-requests/remove",
            self._handle_request_send_remove,
        )
        app.router.add_post(
            "/guilds/{guild_id}/send-requests/update",
            self._handle_request_send_update,
        )
        app.router.add_post("/block", self._handle_block_user)
        app.router.add_post("/unblock", self._handle_unblock_user)
        app.router.add_post(
            "/onboarding/webhook_verified",
            self._handle_onboarding_webhook_verified,
        )

        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, host=self.host, port=self.port)
        await self._site.start()
        log.info("Bot ops server listening on http://%s:%s.", self.host, self.port)
        if not self.secret:
            if self._host_is_loopback(self.host):
                log.warning(
                    "Bot ops server is running without ROB_OPS_SECRET (loopback only). "
                    "Set ROB_OPS_SECRET for defense in depth."
                )
            else:
                log.error(
                    "Bot ops server is bound to %s without ROB_OPS_SECRET; all ops "
                    "requests will be REJECTED. Set ROB_OPS_SECRET.",
                    self.host,
                )

    async def stop(self) -> None:
        if self._runner is None:
            return
        await self._runner.cleanup()
        self._runner = None
        self._site = None

    @staticmethod
    def _host_is_loopback(host: str | None) -> bool:
        return (host or "").strip().lower() in {"127.0.0.1", "::1", "localhost"}

    def _is_authorized(self, request: web.Request) -> bool:
        if self.secret:
            provided = request.headers.get("X-Rob-Ops-Secret", "")
            return hmac.compare_digest(provided, self.secret)
        # Fail closed: with no secret configured, only serve on loopback.
        return self._host_is_loopback(self.host)

    async def _handle_health(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)
        return web.json_response({"ok": True, "bot_user_id": getattr(self.bot.user, "id", None)})

    async def _handle_guild_scan(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)

        guild_id = self._match_guild_id(request)
        if guild_id is None:
            return web.json_response({"error": "invalid_guild_id"}, status=400)
        payload, status = await self._build_guild_scan_payload(guild_id)
        if self._wants_text(request):
            return web.Response(
                text=self._format_guild_scan_text(payload),
                status=status,
                content_type="text/plain",
            )
        return web.json_response(payload, status=status)

    async def _handle_apply_guild_scan(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)
        if not hasattr(self.bot, "vib_settings_repo"):
            return web.json_response({"error": "vib_settings_repo_unavailable"}, status=500)

        guild_id = self._match_guild_id(request)
        if guild_id is None:
            return web.json_response({"error": "invalid_guild_id"}, status=400)

        payload = await self._json_payload(request)
        selected_fields, invalid_options = self._parse_scan_apply_options(payload.get("options"))
        if invalid_options:
            error_payload = {
                "error": "invalid_options",
                "invalid_options": invalid_options,
                "valid_options": ["all", "channels", "roles", *SCAN_APPLY_FIELD_ORDER],
            }
            if self._wants_text(request):
                return web.Response(
                    text=self._format_invalid_scan_options_text(error_payload),
                    status=400,
                    content_type="text/plain",
                )
            return web.json_response(error_payload, status=400)

        scan_payload, status = await self._build_guild_scan_payload(guild_id)
        if status != 200:
            if self._wants_text(request):
                return web.Response(
                    text=self._format_guild_scan_text(scan_payload),
                    status=status,
                    content_type="text/plain",
                )
            return web.json_response(scan_payload, status=status)

        applied: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for entry in [*scan_payload["channel_matches"], *scan_payload["role_matches"]]:
            if entry["field"] not in selected_fields:
                continue

            current = entry["current"]
            suggested = entry["suggested"]
            if suggested is None:
                skipped.append(
                    {
                        "field": entry["field"],
                        "label": entry["label"],
                        "reason": "no_suggestion",
                    }
                )
                continue
            if current["id"] == suggested["id"] and current.get("found", True):
                skipped.append(
                    {
                        "field": entry["field"],
                        "label": entry["label"],
                        "reason": "already_matches",
                        "target_id": suggested["id"],
                        "target_name": suggested["name"],
                    }
                )
                continue

            if entry["type"] == "channel":
                await self.bot.vib_settings_repo.set_channel_id(
                    guild_id,
                    entry["field"],
                    int(suggested["id"]),
                )
            else:
                await self.bot.vib_settings_repo.set_role_id(
                    guild_id,
                    entry["field"],
                    int(suggested["id"]),
                )
            applied.append(
                {
                    "field": entry["field"],
                    "label": entry["label"],
                    "target_type": entry["type"],
                    "target_id": int(suggested["id"]),
                    "target_name": str(suggested["name"]),
                }
            )

        if applied:
            await self._refresh_guild(guild_id)

        result_payload = {
            "ok": True,
            "guild_id": guild_id,
            "guild_name": scan_payload.get("guild_name"),
            "selected_fields": [field for field in SCAN_APPLY_FIELD_ORDER if field in selected_fields],
            "applied": applied,
            "skipped": skipped,
        }
        if self._wants_text(request):
            return web.Response(
                text=self._format_guild_apply_text(result_payload),
                content_type="text/plain",
            )
        return web.json_response(result_payload)

    async def _handle_refresh_public_names(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)

        guild_id = self._match_guild_id(request)
        if guild_id is None:
            return web.json_response({"error": "invalid_guild_id"}, status=400)

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return web.json_response({"error": "guild_not_in_cache", "guild_id": guild_id}, status=404)

        if not hasattr(self.bot, "dommes_repo"):
            return web.json_response({"error": "dommes_repo_unavailable"}, status=500)

        dommes = await self.bot.dommes_repo.list_for_guild(guild_id)
        updated = 0
        for domme in dommes:
            label: str | None = None
            member = guild.get_member(domme.discord_user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(domme.discord_user_id)
                except (discord.NotFound, discord.HTTPException):
                    member = None
            if member is not None:
                label = (member.display_name or member.name or "").strip() or None

            if label:
                await self.bot.dommes_repo.set_public_display_name(
                    guild_id=guild_id,
                    discord_user_id=domme.discord_user_id,
                    label=label,
                )
                updated += 1

        return web.json_response(
            {
                "ok": True,
                "guild_id": guild_id,
                "registered_dommes": len(dommes),
                "updated_display_names": updated,
            }
        )

    async def _handle_refresh_leaderboard(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)

        guild_id = self._match_guild_id(request)
        if guild_id is None:
            return web.json_response({"error": "invalid_guild_id"}, status=400)

        if not hasattr(self.bot, "leaderboard_service"):
            return web.json_response({"error": "leaderboard_service_unavailable"}, status=500)

        refreshed = await self.bot.leaderboard_service.refresh_guild(guild_id)
        return web.json_response({"ok": bool(refreshed), "guild_id": guild_id, "refreshed": bool(refreshed)})

    async def _handle_process_send(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)

        if not hasattr(self.bot, "send_queue_service"):
            return web.json_response({"error": "send_queue_service_unavailable"}, status=500)

        payload = await self._json_payload(request)

        try:
            send_id = int(payload.get("send_id"))
        except (TypeError, ValueError):
            return web.json_response({"error": "invalid_send_id"}, status=400)

        guild_id = payload.get("guild_id")
        try:
            guild_id = int(guild_id) if guild_id is not None else None
        except (TypeError, ValueError):
            return web.json_response({"error": "invalid_guild_id"}, status=400)

        await self.bot.send_queue_service.notify_send(send_id)
        log.info(
            "Accepted send processing notification send_id=%s guild_id=%s.",
            send_id,
            guild_id,
        )
        return web.json_response(
            {
                "ok": True,
                "queued": True,
                "send_id": send_id,
                "guild_id": guild_id,
            }
        )

    async def _handle_onboarding_webhook_verified(
        self, request: web.Request
    ) -> web.Response:
        """Advance an in-progress DM onboarding flow when the Throne test
        webhook arrives. Test-guild-only; the cog also enforces the gate."""

        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)

        payload = await self._json_payload(request)
        try:
            guild_id = int(payload.get("guild_id"))
            discord_user_id = int(payload.get("discord_user_id"))
        except (TypeError, ValueError):
            log.warning(
                "Onboarding webhook auto-advance invalid payload received: %r",
                payload,
            )
            return web.json_response({"error": "invalid_payload"}, status=400)

        log.info(
            "Onboarding webhook auto-advance request received guild_id=%s "
            "discord_user_id=%s",
            guild_id,
            discord_user_id,
        )

        cog = self.bot.get_cog("DMOnboardingCog") if hasattr(self.bot, "get_cog") else None
        if cog is None:
            log.warning(
                "Onboarding webhook auto-advance cog unavailable guild_id=%s "
                "discord_user_id=%s",
                guild_id,
                discord_user_id,
            )
            return web.json_response(
                {"error": "dm_onboarding_cog_unavailable"}, status=500
            )
        try:
            advanced = await cog.on_throne_test_webhook_received(
                guild_id=guild_id,
                discord_user_id=discord_user_id,
            )
        except Exception:
            log.exception(
                "Auto-advance onboarding DM failed guild_id=%s user_id=%s",
                guild_id,
                discord_user_id,
            )
            return web.json_response({"error": "auto_advance_failed"}, status=500)
        log.info(
            "Onboarding webhook auto-advance guild_id=%s user_id=%s advanced=%s",
            guild_id,
            discord_user_id,
            advanced,
        )
        return web.json_response(
            {
                "ok": True,
                "advanced": bool(advanced),
                "guild_id": guild_id,
                "discord_user_id": discord_user_id,
            }
        )

    async def _handle_set_maintenance(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)

        if not hasattr(self.bot, "maintenance_service"):
            return web.json_response({"error": "maintenance_service_unavailable"}, status=500)

        payload = await self._json_payload(request)

        enabled = self._payload_bool(payload, "enabled")
        reason = str(payload.get("reason") or "").strip() or None
        if enabled:
            await self.bot.maintenance_service.enable(reason=reason)
        else:
            await self.bot.maintenance_service.disable()

        state = await self.bot.maintenance_service.get_state()
        payload = {"ok": True, "enabled": state.enabled, "reason": state.reason or ""}
        if self._wants_text(request):
            return web.Response(
                text=self._format_maintenance_text(payload),
                content_type="text/plain",
            )
        return web.json_response(payload)

    async def _handle_get_maintenance(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)
        if not hasattr(self.bot, "maintenance_service"):
            return web.json_response({"error": "maintenance_service_unavailable"}, status=500)
        state = await self.bot.maintenance_service.get_state()
        payload = {"ok": True, "enabled": state.enabled, "reason": state.reason or ""}
        if self._wants_text(request):
            return web.Response(
                text=self._format_maintenance_text(payload),
                content_type="text/plain",
            )
        return web.json_response(payload)


    async def _handle_set_rob_offline(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)
        if not hasattr(self.bot, "maintenance_service"):
            return web.json_response({"error": "maintenance_service_unavailable"}, status=500)

        payload = await self._json_payload(request)
        enabled = self._payload_bool(payload, "enabled")
        if enabled:
            await self.bot.maintenance_service.enable_rob_offline()
        else:
            await self.bot.maintenance_service.disable_rob_offline()

        enabled = await self.bot.maintenance_service.is_rob_offline_enabled()
        payload = {"ok": True, "enabled": enabled}
        if self._wants_text(request):
            return web.Response(
                text=self._format_rob_offline_text(payload),
                content_type="text/plain",
            )
        return web.json_response(payload)

    async def _handle_get_rob_offline(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)
        if not hasattr(self.bot, "maintenance_service"):
            return web.json_response({"error": "maintenance_service_unavailable"}, status=500)
        enabled = await self.bot.maintenance_service.is_rob_offline_enabled()
        payload = {"ok": True, "enabled": enabled}
        if self._wants_text(request):
            return web.Response(
                text=self._format_rob_offline_text(payload),
                content_type="text/plain",
            )
        return web.json_response(payload)

    async def _handle_get_count(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)
        if not hasattr(self.bot, "counting_service"):
            return web.json_response({"error": "counting_service_unavailable"}, status=500)
        guild_id = self._match_guild_id(request)
        if guild_id is None:
            return web.json_response({"error": "invalid_guild_id"}, status=400)
        state = await self.bot.counting_service.get_or_create_state(guild_id)
        payload = {
            "ok": True,
            "guild_id": guild_id,
            "current_number": state.current_number,
            "channel_id": state.channel_id,
            "is_enabled": state.is_enabled,
            "pending_restore": state.pending_restore,
        }
        if self._wants_text(request):
            return web.Response(
                text=self._format_count_text(payload, label="Count Status"),
                content_type="text/plain",
            )
        return web.json_response(payload)

    async def _handle_set_count(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)
        if not hasattr(self.bot, "counting_service"):
            return web.json_response({"error": "counting_service_unavailable"}, status=500)
        guild_id = self._match_guild_id(request)
        if guild_id is None:
            return web.json_response({"error": "invalid_guild_id"}, status=400)
        payload = await self._json_payload(request)
        try:
            number = max(0, int(payload.get("number")))
        except (TypeError, ValueError):
            return web.json_response({"error": "invalid_number"}, status=400)
        state = await self.bot.counting_service.set_current_number(guild_id, number)
        payload = {
            "ok": True,
            "guild_id": guild_id,
            "current_number": state.current_number,
            "channel_id": state.channel_id,
            "is_enabled": state.is_enabled,
        }
        if self._wants_text(request):
            return web.Response(
                text=self._format_count_text(payload, label="Count Updated"),
                content_type="text/plain",
            )
        return web.json_response(payload)

    async def _handle_get_inactivity(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)
        if not hasattr(self.bot, "inactivity_service"):
            return web.json_response({"error": "inactivity_service_unavailable"}, status=500)
        guild_id = self._match_guild_id(request)
        if guild_id is None:
            return web.json_response({"error": "invalid_guild_id"}, status=400)
        enabled = await self.bot.inactivity_service.is_enabled(guild_id)
        watchlist = 0
        if hasattr(self.bot, "inactive_users_repo"):
            watchlist = len(await self.bot.inactive_users_repo.list_for_guild(guild_id))
        payload = {"ok": True, "guild_id": guild_id, "enabled": enabled, "watchlist": watchlist}
        if self._wants_text(request):
            return web.Response(text=self._format_toggle_text(payload, "Inactivity System"), content_type="text/plain")
        return web.json_response(payload)

    async def _handle_set_inactivity(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)
        if not hasattr(self.bot, "inactivity_service"):
            return web.json_response({"error": "inactivity_service_unavailable"}, status=500)
        guild_id = self._match_guild_id(request)
        if guild_id is None:
            return web.json_response({"error": "invalid_guild_id"}, status=400)
        payload = await self._json_payload(request)
        enabled = self._payload_bool(payload, "enabled")
        await self.bot.inactivity_service.set_enabled(guild_id, enabled)
        payload = {"ok": True, "guild_id": guild_id, "enabled": enabled}
        if self._wants_text(request):
            return web.Response(text=self._format_toggle_text(payload, "Inactivity System"), content_type="text/plain")
        return web.json_response(payload)

    async def _handle_inactivity_backfill(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)
        if not hasattr(self.bot, "inactivity_service"):
            return web.json_response({"error": "inactivity_service_unavailable"}, status=500)
        guild_id = self._match_guild_id(request)
        if guild_id is None:
            return web.json_response({"error": "invalid_guild_id"}, status=400)
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return web.json_response({"error": "guild_not_in_cache", "guild_id": guild_id}, status=404)

        payload = await self._json_payload(request)
        try:
            days = int(payload.get("days")) if payload.get("days") not in (None, "") else 7
        except (TypeError, ValueError):
            return web.json_response({"error": "invalid_days"}, status=400)
        days = max(1, min(days, 30))

        service = self.bot.inactivity_service
        summary = await service.backfill_activity_from_history(guild, days=days)
        # Restore Active to now-active members without DMs or kicks; no-op if disabled.
        snapshots = await service.process_guild(guild, send_notifications=False, perform_kicks=False)

        result = {
            "ok": True,
            "guild_id": guild_id,
            "days": days,
            "channels_scanned": summary.get("channels_scanned", 0),
            "members_seen": summary.get("users_seen", 0),
            "members_seeded": summary.get("users_seeded", 0),
            "still_inactive": len(snapshots),
        }
        if self._wants_text(request):
            text = "\n".join(
                [
                    "Rob Control | Inactivity Backfill",
                    f"- Guild ID: {guild_id}",
                    f"- Window: last {days} day(s)",
                    f"- Channels scanned: {result['channels_scanned']}",
                    f"- Active members found: {result['members_seen']}",
                    f"- Activity records seeded: {result['members_seeded']}",
                    f"- Still inactive after backfill: {result['still_inactive']}",
                ]
            )
            return web.Response(text=text + "\n", content_type="text/plain")
        return web.json_response(result)

    async def _handle_get_backup(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)
        if not hasattr(self.bot, "server_backup_service"):
            return web.json_response({"error": "server_backup_service_unavailable"}, status=500)
        guild_id = self._match_guild_id(request)
        if guild_id is None:
            return web.json_response({"error": "invalid_guild_id"}, status=400)
        service = self.bot.server_backup_service
        enabled = await service.is_enabled(guild_id)
        latest = await service.backups.get_latest_backup(guild_id)
        pending = await service.backups.get_pending_approval(guild_id)
        payload = {
            "ok": True,
            "guild_id": guild_id,
            "enabled": enabled,
            "last_backup_at": latest.created_at.isoformat() if latest is not None else None,
            "pending_approval": None
            if pending is None
            else {
                "id": pending.id,
                "approvals": len(pending.approved_by),
                "required": pending.required_approvals,
                "major_changes": len(pending.changes),
            },
        }
        if self._wants_text(request):
            return web.Response(text=self._format_backup_status_text(payload), content_type="text/plain")
        return web.json_response(payload)

    async def _handle_set_backup(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)
        if not hasattr(self.bot, "server_backup_service"):
            return web.json_response({"error": "server_backup_service_unavailable"}, status=500)
        guild_id = self._match_guild_id(request)
        if guild_id is None:
            return web.json_response({"error": "invalid_guild_id"}, status=400)
        payload = await self._json_payload(request)
        enabled = self._payload_bool(payload, "enabled")
        await self.bot.server_backup_service.set_enabled(guild_id, enabled)
        payload = {"ok": True, "guild_id": guild_id, "enabled": enabled}
        if self._wants_text(request):
            return web.Response(text=self._format_toggle_text(payload, "Server Backup System"), content_type="text/plain")
        return web.json_response(payload)

    @staticmethod
    def _format_toggle_text(payload: dict[str, Any], label: str) -> str:
        lines = [f"Rob Control | {label}", f"- Guild ID: {payload['guild_id']}", f"- Enabled: {payload['enabled']}"]
        if "watchlist" in payload:
            lines.append(f"- On Watchlist: {payload['watchlist']}")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _format_backup_status_text(payload: dict[str, Any]) -> str:
        lines = [
            "Rob Control | Server Backup System",
            f"- Guild ID: {payload['guild_id']}",
            f"- Enabled: {payload['enabled']}",
            f"- Last Backup: {payload.get('last_backup_at') or '(none)'}",
        ]
        pending = payload.get("pending_approval")
        if pending is None:
            lines.append("- Pending Approval: (none)")
        else:
            lines.append(
                f"- Pending Approval: id={pending['id']}, "
                f"{pending['approvals']}/{pending['required']} approvals, "
                f"{pending['major_changes']} major change(s)"
            )
        return "\n".join(lines) + "\n"

    async def _handle_set_guild_channel(self, request: web.Request) -> web.Response:
        return await self._handle_set_guild_field(request, kind="channel")

    async def _handle_set_guild_role(self, request: web.Request) -> web.Response:
        return await self._handle_set_guild_field(request, kind="role")

    async def _handle_set_guild_field(self, request: web.Request, *, kind: str) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)
        if not hasattr(self.bot, "vib_settings_repo"):
            return web.json_response({"error": "vib_settings_repo_unavailable"}, status=500)
        guild_id = self._match_guild_id(request)
        if guild_id is None:
            return web.json_response({"error": "invalid_guild_id"}, status=400)

        valid_fields = GUILD_CHANNEL_FIELDS if kind == "channel" else GUILD_ROLE_FIELDS
        id_key = "channel_id" if kind == "channel" else "role_id"
        payload = await self._json_payload(request)
        field = str(payload.get("field") or "").strip()
        if field not in valid_fields:
            return web.json_response(
                {"error": "invalid_field", "valid_fields": list(valid_fields)},
                status=400,
            )

        clear = self._payload_bool(payload, "clear")
        target_id: int | None = None
        if not clear:
            raw = payload.get(id_key)
            try:
                target_id = int(raw)
            except (TypeError, ValueError):
                return web.json_response({"error": f"invalid_{id_key}"}, status=400)

        if kind == "channel":
            updated = await self.bot.vib_settings_repo.set_channel_id(guild_id, field, target_id)
        else:
            updated = await self.bot.vib_settings_repo.set_role_id(guild_id, field, target_id)
        await self._refresh_guild(guild_id)

        stored = getattr(updated, field, None)
        result = {
            "ok": True,
            "guild_id": guild_id,
            "field": field,
            id_key: stored,
        }
        if self._wants_text(request):
            label = "Guild Channel Updated" if kind == "channel" else "Guild Role Updated"
            text = "\n".join(
                [
                    f"Rob Control | {label}",
                    f"- Guild ID: {guild_id}",
                    f"- Field: {field}",
                    f"- {id_key}: {stored if stored is not None else '(cleared)'}",
                ]
            )
            return web.Response(text=text + "\n", content_type="text/plain")
        return web.json_response(result)

    async def _handle_backup_run(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)
        if not hasattr(self.bot, "server_backup_service") or not hasattr(self.bot, "get_cog"):
            return web.json_response({"error": "server_backup_service_unavailable"}, status=500)

        guild_id = self._match_guild_id(request)
        if guild_id is None:
            return web.json_response({"error": "invalid_guild_id"}, status=400)

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return web.json_response({"error": "guild_not_in_cache", "guild_id": guild_id}, status=404)

        cog = self.bot.get_cog("ServerBackupCog")
        if cog is None:
            result = await self.bot.server_backup_service.run_cycle(guild)
        else:
            result = await cog.run_once(guild)

        payload = {
            "ok": True,
            "guild_id": guild_id,
            "action": result.action,
            "change_count": len(result.changes),
            "major_change_count": len(result.major_changes),
            "major_changes": [str(change.get("detail", "")) for change in result.major_changes],
        }
        if self._wants_text(request):
            lines = [
                "Rob Control | Server Backup Run",
                f"- Guild ID: {guild_id}",
                f"- Action: {result.action}",
                f"- Changes: {len(result.changes)} ({len(result.major_changes)} major)",
            ]
            lines.extend(f"  * {detail}" for detail in payload["major_changes"])
            return web.Response(text="\n".join(lines) + "\n", content_type="text/plain")
        return web.json_response(payload)

    async def _handle_migration_audit(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)
        guild_id = self._match_guild_id(request)
        if guild_id is None:
            return web.json_response({"error": "invalid_guild_id"}, status=400)

        payload, status = await self._build_migration_audit_payload(guild_id)
        if self._wants_text(request):
            return web.Response(
                text=self._format_migration_audit_text(payload),
                status=status,
                content_type="text/plain",
            )
        return web.json_response(payload, status=status)

    async def _handle_webhook_reissue_preview(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)
        guild_id = self._match_guild_id(request)
        if guild_id is None:
            return web.json_response({"error": "invalid_guild_id"}, status=400)

        payload, status = await self._build_webhook_reissue_preview_payload(guild_id)
        if self._wants_text(request):
            return web.Response(
                text=self._format_webhook_reissue_preview_text(payload),
                status=status,
                content_type="text/plain",
            )
        return web.json_response(payload, status=status)

    async def _handle_webhook_reissue_send(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)
        if not hasattr(self.bot, "registration_service") or not hasattr(self.bot, "bot_settings_repo"):
            return web.json_response({"error": "webhook_reissue_unavailable"}, status=500)

        guild_id = self._match_guild_id(request)
        if guild_id is None:
            return web.json_response({"error": "invalid_guild_id"}, status=400)

        payload = await self._json_payload(request)
        target_user_id = self._payload_user_id(payload)
        send_all = self._payload_bool(payload, "all")
        try:
            limit = int(payload.get("limit")) if payload.get("limit") not in (None, "") else None
        except (TypeError, ValueError):
            return web.json_response({"error": "invalid_limit"}, status=400)
        if limit is not None and limit <= 0:
            return web.json_response({"error": "invalid_limit"}, status=400)
        if not send_all and target_user_id is None:
            return web.json_response({"error": "missing_target"}, status=400)

        preview_payload, status = await self._build_webhook_reissue_preview_payload(guild_id)
        if status != 200:
            if self._wants_text(request):
                return web.Response(
                    text=self._format_webhook_reissue_preview_text(preview_payload),
                    status=status,
                    content_type="text/plain",
                )
            return web.json_response(preview_payload, status=status)

        rows = list(preview_payload["dommes"])
        if target_user_id is not None:
            rows = [row for row in rows if row["discord_user_id"] == target_user_id]
        else:
            rows = [row for row in rows if row["will_send"]]
        if limit is not None:
            rows = rows[:limit]

        delivered: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        failed: list[dict[str, Any]] = []
        for row in rows:
            if not row["will_send"] and target_user_id is None:
                skipped.append(
                    {
                        "discord_user_id": row["discord_user_id"],
                        "throne_handle": row["throne_handle"],
                        "reason": "already_reissued",
                    }
                )
                continue
            try:
                result = await self.bot.registration_service.reissue_domme_webhook(
                    guild_id=guild_id,
                    discord_user_id=row["discord_user_id"],
                )
                await self._deliver_webhook_reissue_dm(
                    guild_id=guild_id,
                    discord_user_id=row["discord_user_id"],
                    webhook_url=result.webhook_url,
                    domme_id=result.domme.id,
                )
                await self.bot.bot_settings_repo.set_value(
                    self._webhook_reissue_sent_key(guild_id, row["discord_user_id"]),
                    datetime.now(timezone.utc).isoformat(),
                )
                delivered.append(
                    {
                        "discord_user_id": row["discord_user_id"],
                        "throne_handle": result.domme.throne_handle,
                        "webhook_url_generated": bool(result.webhook_url),
                    }
                )
            except Exception as exc:  # pragma: no cover - runtime safety path
                log.exception(
                    "Webhook reissue delivery failed guild_id=%s discord_user_id=%s",
                    guild_id,
                    row["discord_user_id"],
                )
                failed.append(
                    {
                        "discord_user_id": row["discord_user_id"],
                        "throne_handle": row["throne_handle"],
                        "error": str(exc),
                    }
                )

        result_payload = {
            "ok": True,
            "guild_id": guild_id,
            "guild_name": preview_payload.get("guild_name"),
            "requested_all": bool(send_all),
            "requested_user_id": target_user_id,
            "limit": limit,
            "delivered": delivered,
            "skipped": skipped,
            "failed": failed,
        }
        if self._wants_text(request):
            return web.Response(
                text=self._format_webhook_reissue_send_text(result_payload),
                content_type="text/plain",
            )
        return web.json_response(result_payload)

    async def _handle_webhook_reissue_refresh(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)
        if not hasattr(self.bot, "registration_service") or not hasattr(self.bot, "bot_settings_repo"):
            return web.json_response({"error": "webhook_reissue_unavailable"}, status=500)

        guild_id = self._match_guild_id(request)
        if guild_id is None:
            return web.json_response({"error": "invalid_guild_id"}, status=400)

        payload = await self._json_payload(request)
        domme_lookup = str(
            payload.get("domme_lookup")
            or payload.get("throne_handle")
            or payload.get("lookup")
            or ""
        ).strip()
        if not domme_lookup:
            return web.json_response({"error": "missing_domme_lookup"}, status=400)

        domme = await self._resolve_domme(guild_id, domme_lookup)
        if domme is None:
            return web.json_response({"error": "domme_not_found"}, status=404)

        try:
            result = await self.bot.registration_service.reissue_domme_webhook(
                guild_id=guild_id,
                discord_user_id=domme.discord_user_id,
            )
            await self._deliver_webhook_reissue_dm(
                guild_id=guild_id,
                discord_user_id=domme.discord_user_id,
                webhook_url=result.webhook_url,
                domme_id=result.domme.id,
                mode="refresh",
                throne_name=result.domme.throne_handle or domme_lookup,
            )
            await self.bot.bot_settings_repo.set_value(
                self._webhook_reissue_sent_key(guild_id, domme.discord_user_id),
                datetime.now(timezone.utc).isoformat(),
            )
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except Exception as exc:  # pragma: no cover - runtime safety path
            log.exception(
                "Webhook refresh delivery failed guild_id=%s domme_lookup=%s discord_user_id=%s",
                guild_id,
                domme_lookup,
                getattr(domme, "discord_user_id", None),
            )
            return web.json_response({"error": str(exc)}, status=500)

        guild = self.bot.get_guild(guild_id)
        response_payload = {
            "ok": True,
            "guild_id": guild_id,
            "guild_name": guild.name if guild is not None else None,
            "discord_user_id": domme.discord_user_id,
            "throne_handle": result.domme.throne_handle,
            "webhook_url_generated": bool(result.webhook_url),
        }
        if self._wants_text(request):
            return web.Response(
                text=self._format_webhook_reissue_refresh_text(response_payload),
                content_type="text/plain",
            )
        return web.json_response(response_payload)

    async def _handle_add_domme(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)
        if not hasattr(self.bot, "registration_service"):
            return web.json_response({"error": "registration_service_unavailable"}, status=500)
        guild_id = self._match_guild_id(request)
        if guild_id is None:
            return web.json_response({"error": "invalid_guild_id"}, status=400)
        payload = await self._json_payload(request)
        discord_user_id = self._payload_user_id(payload)
        throne_input = str(payload.get("throne_input") or "").strip()
        if discord_user_id is None:
            return web.json_response({"error": "invalid_discord_user_id"}, status=400)
        if not throne_input:
            return web.json_response({"error": "missing_throne_input"}, status=400)
        try:
            result = await self.bot.registration_service.register_domme(
                guild_id=guild_id,
                discord_user_id=discord_user_id,
                throne_input=throne_input,
            )
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        await self._refresh_guild(guild_id)
        payload = {
            "ok": True,
            "guild_id": guild_id,
            "discord_user_id": result.domme.discord_user_id,
            "domme_id": result.domme.id,
            "throne_handle": result.domme.throne_handle,
            "throne_creator_id": result.domme.throne_creator_id,
            "webhook_url": result.webhook_url,
        }
        if self._wants_text(request):
            return web.Response(
                text=self._format_domme_change_text(payload, added=True),
                content_type="text/plain",
            )
        return web.json_response(payload)

    async def _handle_remove_domme(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)
        if not hasattr(self.bot, "dommes_repo"):
            return web.json_response({"error": "dommes_repo_unavailable"}, status=500)
        guild_id = self._match_guild_id(request)
        if guild_id is None:
            return web.json_response({"error": "invalid_guild_id"}, status=400)
        payload = await self._json_payload(request)
        target = str(payload.get("target") or "").strip()
        if not target:
            return web.json_response({"error": "missing_target"}, status=400)
        domme = await self._resolve_domme(guild_id, target)
        if domme is None:
            return web.json_response({"error": "domme_not_found"}, status=404)
        removed = await self.bot.dommes_repo.remove_by_user_id(guild_id, domme.discord_user_id)
        if removed is None:
            return web.json_response({"error": "domme_not_found"}, status=404)
        await self._refresh_guild(guild_id)
        payload = {
            "ok": True,
            "guild_id": guild_id,
            "discord_user_id": removed.discord_user_id,
            "throne_handle": removed.throne_handle,
        }
        if self._wants_text(request):
            return web.Response(
                text=self._format_domme_change_text(payload, added=False),
                content_type="text/plain",
            )
        return web.json_response(payload)

    async def _handle_add_sub(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)
        if not hasattr(self.bot, "registration_service"):
            return web.json_response({"error": "registration_service_unavailable"}, status=500)
        guild_id = self._match_guild_id(request)
        if guild_id is None:
            return web.json_response({"error": "invalid_guild_id"}, status=400)
        payload = await self._json_payload(request)
        discord_user_id = self._payload_user_id(payload)
        send_names = self._payload_send_names(payload)
        if discord_user_id is None:
            return web.json_response({"error": "invalid_discord_user_id"}, status=400)
        if not send_names:
            return web.json_response({"error": "missing_send_names"}, status=400)
        try:
            result = await self.bot.registration_service.register_sub(
                guild_id=guild_id,
                discord_user_id=discord_user_id,
                send_names=[str(value) for value in send_names],
            )
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        await self._refresh_guild(guild_id)
        payload = {
            "ok": True,
            "guild_id": guild_id,
            "discord_user_id": result.sub.discord_user_id,
            "sub_id": result.sub.id,
            "send_names": list(result.send_names),
        }
        if self._wants_text(request):
            return web.Response(
                text=self._format_sub_change_text(payload, added=True),
                content_type="text/plain",
            )
        return web.json_response(payload)

    async def _handle_remove_sub(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)
        if not hasattr(self.bot, "subs_repo"):
            return web.json_response({"error": "subs_repo_unavailable"}, status=500)
        guild_id = self._match_guild_id(request)
        if guild_id is None:
            return web.json_response({"error": "invalid_guild_id"}, status=400)
        payload = await self._json_payload(request)
        target = str(payload.get("target") or "").strip()
        if not target:
            return web.json_response({"error": "missing_target"}, status=400)
        removed = None
        if target.isdigit():
            removed = await self.bot.subs_repo.remove_by_user_id(guild_id, int(target))
        if removed is None:
            removed = await self.bot.subs_repo.remove_by_send_name(guild_id, target)
        if removed is None:
            return web.json_response({"error": "sub_not_found"}, status=404)
        await self._refresh_guild(guild_id)
        payload = {
            "ok": True,
            "guild_id": guild_id,
            "discord_user_id": removed.discord_user_id,
            "send_name": removed.send_name,
        }
        if self._wants_text(request):
            return web.Response(
                text=self._format_sub_change_text(payload, added=False),
                content_type="text/plain",
            )
        return web.json_response(payload)

    async def _handle_request_send_add(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)
        if not hasattr(self.bot, "send_change_request_service"):
            return web.json_response({"error": "send_change_request_service_unavailable"}, status=500)
        guild_id = self._match_guild_id(request)
        if guild_id is None:
            return web.json_response({"error": "invalid_guild_id"}, status=400)
        payload = await self._json_payload(request)
        domme_lookup = str(payload.get("domme_lookup") or "").strip()
        requested_by = str(payload.get("requested_by") or "rob-cli").strip() or "rob-cli"
        sub_name = str(payload.get("sub_name") or "").strip() or None
        note = str(payload.get("note") or "").strip() or None
        method = str(payload.get("method") or "manual").strip() or "manual"
        currency = str(payload.get("currency") or "USD").strip().upper() or "USD"
        if not domme_lookup:
            return web.json_response({"error": "missing_domme_lookup"}, status=400)
        try:
            amount = float(payload.get("amount"))
        except (TypeError, ValueError):
            return web.json_response({"error": "invalid_amount"}, status=400)
        if amount <= 0:
            return web.json_response({"error": "invalid_amount"}, status=400)
        try:
            change_request = await self.bot.send_change_request_service.create_send_add_request(
                guild_id=guild_id,
                domme_lookup=domme_lookup,
                amount_cents=dollars_to_cents(amount),
                sub_name=sub_name,
                requested_by=requested_by,
                currency=currency,
                method=method,
                note=note,
            )
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except Exception:
            log.exception("Send add approval request failed guild_id=%s domme_lookup=%s", guild_id, domme_lookup)
            return web.json_response(
                {"error": "Rob could not create the send approval request just now."},
                status=500,
            )
        payload = {
            "ok": True,
            "request_id": change_request.id,
            "action": change_request.action,
            "status": change_request.status,
            "domme_user_id": change_request.domme_user_id,
        }
        if self._wants_text(request):
            return web.Response(
                text=self._format_send_request_text(payload),
                content_type="text/plain",
            )
        return web.json_response(payload)

    async def _handle_request_send_remove(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)
        if not hasattr(self.bot, "send_change_request_service"):
            return web.json_response({"error": "send_change_request_service_unavailable"}, status=500)
        guild_id = self._match_guild_id(request)
        if guild_id is None:
            return web.json_response({"error": "invalid_guild_id"}, status=400)
        payload = await self._json_payload(request)
        domme_lookup = str(payload.get("domme_lookup") or "").strip()
        requested_by = str(payload.get("requested_by") or "rob-cli").strip() or "rob-cli"
        if not domme_lookup:
            return web.json_response({"error": "missing_domme_lookup"}, status=400)
        try:
            send_id = int(payload.get("send_id"))
        except (TypeError, ValueError):
            return web.json_response({"error": "invalid_send_id"}, status=400)
        try:
            change_request = await self.bot.send_change_request_service.create_send_remove_request(
                guild_id=guild_id,
                domme_lookup=domme_lookup,
                send_id=send_id,
                requested_by=requested_by,
            )
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except Exception:
            log.exception(
                "Send remove approval request failed guild_id=%s domme_lookup=%s send_id=%s",
                guild_id,
                domme_lookup,
                send_id,
            )
            return web.json_response(
                {"error": "Rob could not create the send removal approval request just now."},
                status=500,
            )
        payload = {
            "ok": True,
            "request_id": change_request.id,
            "action": change_request.action,
            "status": change_request.status,
            "domme_user_id": change_request.domme_user_id,
            "target_send_id": change_request.target_send_id,
        }
        if self._wants_text(request):
            return web.Response(
                text=self._format_send_request_text(payload),
                content_type="text/plain",
            )
        return web.json_response(payload)

    async def _handle_request_send_update(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)
        if not hasattr(self.bot, "send_change_request_service"):
            return web.json_response({"error": "send_change_request_service_unavailable"}, status=500)
        guild_id = self._match_guild_id(request)
        if guild_id is None:
            return web.json_response({"error": "invalid_guild_id"}, status=400)
        payload = await self._json_payload(request)
        domme_lookup = str(payload.get("domme_lookup") or "").strip()
        requested_by = str(payload.get("requested_by") or "rob-cli").strip() or "rob-cli"
        reason = str(payload.get("reason") or "").strip()
        if not domme_lookup:
            return web.json_response({"error": "missing_domme_lookup"}, status=400)
        if not reason:
            return web.json_response({"error": "missing_reason"}, status=400)
        try:
            send_id = int(payload.get("send_id"))
            message_id = int(payload.get("message_id"))
            amount = float(payload.get("amount"))
        except (TypeError, ValueError):
            return web.json_response({"error": "invalid_update_payload"}, status=400)
        if amount <= 0:
            return web.json_response({"error": "invalid_amount"}, status=400)
        try:
            change_request = await self.bot.send_change_request_service.create_send_update_request(
                guild_id=guild_id,
                domme_lookup=domme_lookup,
                send_id=send_id,
                amount_cents=dollars_to_cents(amount),
                message_id=message_id,
                reason=reason,
                requested_by=requested_by,
                # rob send update amount input is an operator-provided USD override.
                currency="USD",
            )
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except Exception:
            log.exception(
                "Send update approval request failed guild_id=%s domme_lookup=%s send_id=%s",
                guild_id,
                domme_lookup,
                send_id,
            )
            return web.json_response(
                {"error": "Rob could not create the send update approval request just now."},
                status=500,
            )
        payload = {
            "ok": True,
            "request_id": change_request.id,
            "action": change_request.action,
            "status": change_request.status,
            "domme_user_id": change_request.domme_user_id,
            "target_send_id": change_request.target_send_id,
        }
        if self._wants_text(request):
            return web.Response(
                text=self._format_send_request_text(payload),
                content_type="text/plain",
            )
        return web.json_response(payload)

    async def _handle_block_user(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)
        if not hasattr(self.bot, "blacklist_repo"):
            return web.json_response({"error": "blacklist_repo_unavailable"}, status=500)
        payload = await self._json_payload(request)
        discord_user_id = self._payload_user_id(payload)
        if discord_user_id is None:
            return web.json_response({"error": "invalid_discord_user_id"}, status=400)
        reason = str(payload.get("reason") or "rob-cli block").strip() or "rob-cli block"
        await self.bot.blacklist_repo.add(
            discord_user_id=discord_user_id,
            reason=reason,
            created_by=None,
            guild_id=0,
        )
        payload = {"ok": True, "discord_user_id": discord_user_id, "blocked": True}
        if self._wants_text(request):
            return web.Response(
                text=self._format_block_text(payload),
                content_type="text/plain",
            )
        return web.json_response(payload)

    async def _handle_unblock_user(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)
        if not hasattr(self.bot, "blacklist_repo"):
            return web.json_response({"error": "blacklist_repo_unavailable"}, status=500)
        payload = await self._json_payload(request)
        discord_user_id = self._payload_user_id(payload)
        if discord_user_id is None:
            return web.json_response({"error": "invalid_discord_user_id"}, status=400)
        await self.bot.blacklist_repo.remove(discord_user_id)
        payload = {"ok": True, "discord_user_id": discord_user_id, "blocked": False}
        if self._wants_text(request):
            return web.Response(
                text=self._format_block_text(payload),
                content_type="text/plain",
            )
        return web.json_response(payload)

    async def _build_guild_scan_payload(self, guild_id: int) -> tuple[dict[str, Any], int]:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return (
                {
                    "guild_id": guild_id,
                    "guild_name": None,
                    "channels": [],
                    "roles": [],
                    "channel_matches": [],
                    "role_matches": [],
                    "source": "bot-session",
                    "error": "Guild is not currently available in the running bot cache.",
                },
                404,
            )

        settings = None
        if hasattr(self.bot, "vib_settings_repo"):
            settings = await self.bot.vib_settings_repo.get(guild_id)

        channels = [
            {
                "id": int(channel.id),
                "name": str(channel.name),
                "kind": type(channel).__name__,
            }
            for channel in sorted(guild.channels, key=lambda item: (item.name.lower(), item.id))
            if isinstance(channel, discord.TextChannel)
        ]
        roles = [
            {
                "id": int(role.id),
                "name": str(role.name),
            }
            for role in sorted(guild.roles, key=lambda item: (item.name.lower(), item.id))
            if role.name != "@everyone"
        ]

        channel_lookup = {int(channel["id"]): channel for channel in channels}
        role_lookup = {int(role["id"]): role for role in roles}

        channel_matches: list[dict[str, Any]] = []
        for field_name in GUILD_CHANNEL_FIELDS:
            configured_id = getattr(settings, field_name, None) if settings is not None else None
            current = channel_lookup.get(configured_id) if configured_id is not None else None
            suggested = _find_best_channel_match(channels, field_name)
            channel_matches.append(
                {
                    "type": "channel",
                    "field": field_name,
                    "label": GUILD_CHANNEL_LABELS[field_name],
                    "current": {
                        "id": configured_id,
                        "name": current["name"] if current is not None else None,
                        "kind": current["kind"] if current is not None else None,
                        "found": current is not None,
                    },
                    "suggested": suggested,
                }
            )

        role_matches: list[dict[str, Any]] = []
        for field_name in GUILD_ROLE_FIELDS:
            configured_id = getattr(settings, field_name, None) if settings is not None else None
            current = role_lookup.get(configured_id) if configured_id is not None else None
            suggested = _find_best_role_match(roles, field_name)
            role_matches.append(
                {
                    "type": "role",
                    "field": field_name,
                    "label": GUILD_ROLE_LABELS[field_name],
                    "current": {
                        "id": configured_id,
                        "name": current["name"] if current is not None else None,
                        "found": current is not None,
                    },
                    "suggested": suggested,
                }
            )

        return (
            {
                "guild_id": int(guild.id),
                "guild_name": guild.name,
                "channels": channels,
                "roles": roles,
                "channel_matches": channel_matches,
                "role_matches": role_matches,
                "source": "bot-session",
            },
            200,
        )

    @staticmethod
    def _webhook_reissue_sent_key(guild_id: int, discord_user_id: int) -> str:
        return f"{WEBHOOK_REISSUE_SENT_PREFIX}:{guild_id}:{discord_user_id}"

    @staticmethod
    def _domme_needs_reconnect(domme: Any) -> bool:
        return (
            not domme.webhook_connected_at
            or not domme.last_successful_event_at
            or str(domme.tracking_status).strip().lower() != "active"
        )

    @staticmethod
    def _domme_preview_label(domme: Any) -> str:
        return (
            getattr(domme, "throne_handle", None)
            or _preview_handle_from_url(getattr(domme, "throne_url", None))
            or getattr(domme, "public_display_name", None)
            or getattr(domme, "throne_creator_id", None)
            or "(missing)"
        )

    async def _build_webhook_reissue_preview_payload(self, guild_id: int) -> tuple[dict[str, Any], int]:
        if not hasattr(self.bot, "dommes_repo") or not hasattr(self.bot, "bot_settings_repo"):
            return (
                {
                    "guild_id": guild_id,
                    "guild_name": None,
                    "dommes": [],
                    "error": "dommes_repo_or_bot_settings_repo_unavailable",
                },
                500,
            )

        guild = self.bot.get_guild(guild_id)
        guild_name = guild.name if guild is not None else None
        rows: list[dict[str, Any]] = []
        reissue_ready = 0
        reconnect_needed = 0
        for domme in await self.bot.dommes_repo.list_for_guild(guild_id):
            sent_marker = await self.bot.bot_settings_repo.get_text(
                self._webhook_reissue_sent_key(guild_id, domme.discord_user_id)
            )
            already_reissued = sent_marker is not None
            needs_reconnect = self._domme_needs_reconnect(domme)
            if needs_reconnect:
                reconnect_needed += 1
            if not already_reissued:
                reissue_ready += 1
            rows.append(
                {
                    "discord_user_id": domme.discord_user_id,
                    "throne_handle": domme.throne_handle,
                    "preview_label": self._domme_preview_label(domme),
                    "tracking_status": domme.tracking_status,
                    "profile_status": domme.profile_status,
                    "webhook_connected": domme.webhook_connected_at is not None,
                    "last_successful_event": domme.last_successful_event_at is not None,
                    "already_reissued": already_reissued,
                    "reissue_sent_at": sent_marker,
                    "needs_reconnect": needs_reconnect,
                    "will_send": not already_reissued,
                }
            )
        rows.sort(key=lambda row: (row["already_reissued"], row["discord_user_id"]))
        return (
            {
                "ok": True,
                "guild_id": guild_id,
                "guild_name": guild_name,
                "dommes": rows,
                "domme_count": len(rows),
                "pending_reissue_count": reissue_ready,
                "already_reissued_count": len(rows) - reissue_ready,
                "reconnect_needed_count": reconnect_needed,
            },
            200,
        )

    async def _build_migration_audit_payload(self, guild_id: int) -> tuple[dict[str, Any], int]:
        required = ("dommes_repo", "subs_repo", "sends_repo", "leaderboards_repo", "counting_service", "maintenance_service")
        if any(not hasattr(self.bot, attr) for attr in required):
            return (
                {
                    "guild_id": guild_id,
                    "guild_name": None,
                    "error": "migration_audit_dependencies_unavailable",
                },
                500,
            )

        guild = self.bot.get_guild(guild_id)
        guild_name = guild.name if guild is not None else None
        summary = await self.bot.leaderboards_repo.get_summary(guild_id)
        count_state = await self.bot.counting_service.get_or_create_state(guild_id)
        maintenance_state = await self.bot.maintenance_service.get_state()
        preview_payload, _status = await self._build_webhook_reissue_preview_payload(guild_id)
        leaderboard_main = await self.bot.leaderboards_repo.get_message(guild_id, "leaderboard")
        leaderboard_stats = await self.bot.leaderboards_repo.get_message(guild_id, "leaderboard_stats")
        payload = {
            "ok": True,
            "guild_id": guild_id,
            "guild_name": guild_name,
            "domme_count": await self.bot.dommes_repo.count(guild_id),
            "sub_count": len(await self.bot.subs_repo.list_for_guild(guild_id)),
            "send_count": await self.bot.sends_repo.count_for_guild(guild_id),
            "send_total_cents": await self.bot.sends_repo.total_cents_for_guild(guild_id),
            "leaderboard_totals_counted_sends": summary.send_count,
            "leaderboard_totals_counted_amount_cents": summary.total_cents,
            "current_number": count_state.current_number,
            "count_channel_id": count_state.channel_id,
            "count_enabled": count_state.is_enabled,
            "maintenance_enabled": maintenance_state.enabled,
            "maintenance_reason": maintenance_state.reason or "",
            "leaderboard_message_id": leaderboard_main.message_id if leaderboard_main is not None else None,
            "leaderboard_channel_id": leaderboard_main.channel_id if leaderboard_main is not None else None,
            "stats_message_id": leaderboard_stats.message_id if leaderboard_stats is not None else None,
            "stats_channel_id": leaderboard_stats.channel_id if leaderboard_stats is not None else None,
            "pending_reissue_count": preview_payload.get("pending_reissue_count", 0),
            "already_reissued_count": preview_payload.get("already_reissued_count", 0),
            "reconnect_needed_count": preview_payload.get("reconnect_needed_count", 0),
        }
        return payload, 200

    async def _deliver_webhook_reissue_dm(
        self,
        *,
        guild_id: int,
        discord_user_id: int,
        webhook_url: str | None,
        domme_id: int,
        mode: str = "migration",
        throne_name: str | None = None,
    ) -> None:
        if webhook_url is None:
            raise ValueError("Webhook URL generation is unavailable.")
        user = self.bot.get_user(discord_user_id)
        if user is None:
            user = await self.bot.fetch_user(discord_user_id)

        settings = await self.bot.guild_settings_repo.get(guild_id)
        from rob.discord.cogs.registration import NotYetButton, YesButton
        from rob.ui.cards.registration import throne_setup_card
        from rob.ui.copy import (
            WEBHOOK_REFRESH_TITLE,
            WEBHOOK_REISSUE_TITLE,
            webhook_refresh_message,
            webhook_upgrade_message,
        )
        from rob.ui.render import add_card_actions

        if mode == "refresh":
            title = WEBHOOK_REFRESH_TITLE
            description = webhook_refresh_message(webhook_url)
        else:
            title = WEBHOOK_REISSUE_TITLE
            description = webhook_upgrade_message(
                throne_name=throne_name or "there",
                webhook_url=webhook_url,
            )

        rendered = throne_setup_card(description, title=title)
        add_card_actions(
            rendered.view,
            YesButton(
                domme_id=domme_id,
                send_track_channel_id=settings.send_track_channel_id if settings is not None else None,
            ),
            NotYetButton(),
        )
        await user.send(**rendered.send_kwargs())

    @staticmethod
    def _wants_text(request: web.Request) -> bool:
        return request.query.get("format", "").strip().lower() == "text"

    @staticmethod
    def _parse_scan_apply_options(raw: Any) -> tuple[set[str], list[str]]:
        if raw is None:
            return set(SCAN_APPLY_FIELD_ORDER), []

        if isinstance(raw, list):
            parts = ",".join(str(item) for item in raw)
        else:
            parts = str(raw)
        if not parts.strip():
            return set(SCAN_APPLY_FIELD_ORDER), []

        selected: set[str] = set()
        invalid: list[str] = []
        for raw_token in parts.split(","):
            token = raw_token.strip()
            if not token:
                continue
            normalized = token.casefold()
            if normalized == "all":
                return set(SCAN_APPLY_FIELD_ORDER), []
            if normalized == "channels":
                selected.update(GUILD_CHANNEL_FIELDS)
                continue
            if normalized == "roles":
                selected.update(GUILD_ROLE_FIELDS)
                continue
            if normalized in SCAN_APPLY_FIELD_ORDER:
                selected.add(normalized)
                continue
            invalid.append(token)

        if not selected and not invalid:
            selected.update(SCAN_APPLY_FIELD_ORDER)
        return selected, invalid

    @staticmethod
    def _format_guild_scan_text(payload: dict[str, Any]) -> str:
        lines = [
            "Guild Scan",
            f"Guild ID: {payload['guild_id']}",
            f"Guild Name: {payload.get('guild_name') or '(unknown)'}",
            f"Live Text Channels: {len(payload.get('channels', []))}",
            f"Live Roles: {len(payload.get('roles', []))}",
            f"Live Source: {payload.get('source', 'bot-session')}",
        ]
        if payload.get("error"):
            lines.append(f"Live Scan: {payload['error']}")

        channel_matches = payload.get("channel_matches") or []
        if channel_matches:
            lines.extend(["", "Channels"])
            for entry in channel_matches:
                lines.append(f"{entry['label']}:")
                lines.append(f"  current: {BotOpsServer._format_scan_current(entry)}")
                lines.append(f"  suggested: {BotOpsServer._format_scan_suggested(entry)}")
                suggested = entry.get("suggested")
                current = entry.get("current") or {}
                if suggested is not None and (
                    current.get("id") != suggested.get("id") or not current.get("found", True)
                ):
                    lines.append(
                        "  auto-apply: "
                        f"rob auto-apply --guild {payload['guild_id']} {entry['field']}"
                    )

        role_matches = payload.get("role_matches") or []
        if role_matches:
            lines.extend(["", "Roles"])
            for entry in role_matches:
                lines.append(f"{entry['label']}:")
                lines.append(f"  current: {BotOpsServer._format_scan_current(entry)}")
                lines.append(f"  suggested: {BotOpsServer._format_scan_suggested(entry)}")
                suggested = entry.get("suggested")
                current = entry.get("current") or {}
                if suggested is not None and (
                    current.get("id") != suggested.get("id") or not current.get("found", True)
                ):
                    lines.append(
                        "  auto-apply: "
                        f"rob auto-apply --guild {payload['guild_id']} {entry['field']}"
                    )
        return "\n".join(lines)

    @staticmethod
    def _format_scan_current(entry: dict[str, Any]) -> str:
        current = entry.get("current") or {}
        current_id = current.get("id")
        if current_id is None:
            return "(not set)"
        if not current.get("found", False):
            return f"{current_id} (not found in live guild scan)"
        if entry.get("type") == "channel":
            return f"#{current.get('name')} ({current_id})"
        return f"@{current.get('name')} ({current_id})"

    @staticmethod
    def _format_scan_suggested(entry: dict[str, Any]) -> str:
        suggested = entry.get("suggested")
        if suggested is None:
            return "(no obvious match found)"
        if entry.get("type") == "channel":
            return f"#{suggested.get('name')} ({suggested.get('id')})"
        return f"@{suggested.get('name')} ({suggested.get('id')})"

    @staticmethod
    def _format_guild_apply_text(payload: dict[str, Any]) -> str:
        lines = [
            "Guild Auto-Apply",
            f"Guild ID: {payload['guild_id']}",
            f"Guild Name: {payload.get('guild_name') or '(unknown)'}",
            "Selected: "
            + (
                ", ".join(payload.get("selected_fields", []))
                if payload.get("selected_fields")
                else "all"
            ),
        ]
        applied = payload.get("applied") or []
        skipped = payload.get("skipped") or []

        lines.append("")
        lines.append("Applied")
        if not applied:
            lines.append("- nothing changed")
        else:
            for entry in applied:
                prefix = "#" if entry.get("target_type") == "channel" else "@"
                lines.append(
                    f"- {entry['label']}: {prefix}{entry['target_name']} ({entry['target_id']})"
                )

        lines.append("")
        lines.append("Skipped")
        if not skipped:
            lines.append("- nothing skipped")
        else:
            for entry in skipped:
                if entry["reason"] == "already_matches":
                    lines.append(
                        f"- {entry['label']}: already matches {entry['target_name']} ({entry['target_id']})"
                    )
                elif entry["reason"] == "no_suggestion":
                    lines.append(f"- {entry['label']}: no obvious match found")
                else:
                    lines.append(f"- {entry['label']}: {entry['reason']}")

        return "\n".join(lines)

    @staticmethod
    def _format_count_text(payload: dict[str, Any], *, label: str) -> str:
        lines = [
            label,
            f"Guild ID: {payload['guild_id']}",
            f"Current Number: {payload['current_number']}",
            f"Counting Enabled: {'yes' if payload.get('is_enabled') else 'no'}",
            "Counting Channel: "
            + (
                f"{payload['channel_id']}"
                if payload.get("channel_id") is not None
                else "(not configured)"
            ),
        ]
        if "pending_restore" in payload:
            lines.append(
                f"Recovery Window Active: {'yes' if payload.get('pending_restore') else 'no'}"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_migration_audit_text(payload: dict[str, Any]) -> str:
        lines = [
            "Migration Audit",
            f"Guild ID: {payload['guild_id']}",
            f"Guild Name: {payload.get('guild_name') or '(unknown)'}",
        ]
        if payload.get("error"):
            lines.append(f"Error: {payload['error']}")
            return "\n".join(lines)
        lines.extend(
            [
                f"Registered Dom/mes: {payload['domme_count']}",
                f"Registered Subs: {payload['sub_count']}",
                f"Tracked Sends: {payload['send_count']}",
                f"Tracked Total: {format_money_from_cents(payload['send_total_cents'])}",
                f"Counted Leaderboard Sends: {payload['leaderboard_totals_counted_sends']}",
                "Counted Leaderboard Total: "
                + format_money_from_cents(payload["leaderboard_totals_counted_amount_cents"]),
                f"Current Count Number: {payload['current_number']}",
                "Counting Channel: "
                + (
                    str(payload["count_channel_id"])
                    if payload.get("count_channel_id") is not None
                    else "(not configured)"
                ),
                f"Counting Enabled: {'yes' if payload.get('count_enabled') else 'no'}",
                f"Maintenance Enabled: {'yes' if payload.get('maintenance_enabled') else 'no'}",
                "Maintenance Reason: " + (payload.get("maintenance_reason") or "(none)"),
                "Leaderboard Message: "
                + (
                    f"{payload['leaderboard_channel_id']} / {payload['leaderboard_message_id']}"
                    if payload.get("leaderboard_message_id") is not None
                    else "(missing)"
                ),
                "Stats Message: "
                + (
                    f"{payload['stats_channel_id']} / {payload['stats_message_id']}"
                    if payload.get("stats_message_id") is not None
                    else "(missing)"
                ),
                f"Webhook Reissue Pending: {payload['pending_reissue_count']}",
                f"Webhook Reissue Already Sent: {payload['already_reissued_count']}",
                f"Webhook Reconnect Needed: {payload['reconnect_needed_count']}",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _format_webhook_reissue_preview_text(payload: dict[str, Any]) -> str:
        lines = [
            "Webhook Reissue Preview",
            f"Guild ID: {payload['guild_id']}",
            f"Guild Name: {payload.get('guild_name') or '(unknown)'}",
        ]
        if payload.get("error"):
            lines.append(f"Error: {payload['error']}")
            return "\n".join(lines)
        lines.extend(
            [
                f"Registered Dom/mes: {payload['domme_count']}",
                f"Pending Reissue: {payload['pending_reissue_count']}",
                f"Already Reissued: {payload['already_reissued_count']}",
                f"Reconnect Needed: {payload['reconnect_needed_count']}",
                "",
                "Recipients",
            ]
        )
        if not payload.get("dommes"):
            lines.append("- no registered Dom/mes found")
            return "\n".join(lines)
        for row in payload["dommes"]:
            lines.append(
                "- "
                f"user={row['discord_user_id']} "
                f"handle={row.get('preview_label') or row.get('throne_handle') or '(missing)'} "
                f"tracking={row['tracking_status']} "
                f"connected={'yes' if row['webhook_connected'] else 'no'} "
                f"reconnect_needed={'yes' if row['needs_reconnect'] else 'no'} "
                f"action={'send_new_url' if row['will_send'] else 'skip_already_reissued'}"
            )
        return "\n".join(lines)

    @staticmethod
    def _format_webhook_reissue_send_text(payload: dict[str, Any]) -> str:
        lines = [
            "Webhook Reissue Send",
            f"Guild ID: {payload['guild_id']}",
            f"Guild Name: {payload.get('guild_name') or '(unknown)'}",
            f"Delivered: {len(payload.get('delivered') or [])}",
            f"Skipped: {len(payload.get('skipped') or [])}",
            f"Failed: {len(payload.get('failed') or [])}",
        ]
        delivered = payload.get("delivered") or []
        if delivered:
            lines.extend(["", "Delivered"])
            for row in delivered:
                lines.append(
                    f"- user={row['discord_user_id']} handle={row.get('preview_label') or row.get('throne_handle') or '(missing)'}"
                )
        skipped = payload.get("skipped") or []
        if skipped:
            lines.extend(["", "Skipped"])
            for row in skipped:
                lines.append(
                    f"- user={row['discord_user_id']} handle={row.get('preview_label') or row.get('throne_handle') or '(missing)'} reason={row['reason']}"
                )
        failed = payload.get("failed") or []
        if failed:
            lines.extend(["", "Failed"])
            for row in failed:
                lines.append(
                    f"- user={row['discord_user_id']} handle={row.get('preview_label') or row.get('throne_handle') or '(missing)'} error={row['error']}"
                )
        return "\n".join(lines)

    @staticmethod
    def _format_webhook_reissue_refresh_text(payload: dict[str, Any]) -> str:
        return "\n".join(
            [
                "Webhook URL Refreshed",
                f"Guild ID: {payload['guild_id']}",
                f"Guild Name: {payload.get('guild_name') or '(unknown)'}",
                f"Discord User ID: {payload['discord_user_id']}",
                "Throne Handle: " + (payload.get("throne_handle") or "(missing)"),
                "DM Sent: yes",
            ]
        )

    @staticmethod
    def _format_maintenance_text(payload: dict[str, Any]) -> str:
        reason = BotOpsServer._display_text(payload.get("reason")) or "(none)"
        return "\n".join(
            [
                "Maintenance Status",
                f"Enabled: {'yes' if payload.get('enabled') else 'no'}",
                "Reason: " + reason,
            ]
        )

    @staticmethod
    def _format_rob_offline_text(payload: dict[str, Any]) -> str:
        return "\n".join(
            [
                "Rob Offline Mode",
                f"Enabled: {'yes' if payload.get('enabled') else 'no'}",
                "Scope: main guild only",
            ]
        )

    @staticmethod
    def _format_domme_change_text(payload: dict[str, Any], *, added: bool) -> str:
        lines = [
            "Dom/me Added" if added else "Dom/me Removed",
            f"Guild ID: {payload['guild_id']}",
            f"Discord User ID: {payload['discord_user_id']}",
            "Throne Handle: " + (payload.get("throne_handle") or "(not set)"),
        ]
        if added:
            lines.append(f"Dom/me ID: {payload['domme_id']}")
            lines.append("Creator ID: " + (payload.get("throne_creator_id") or "(not set)"))
            if payload.get("webhook_url"):
                lines.append("Webhook URL: generated")
        return "\n".join(lines)

    @staticmethod
    def _format_sub_change_text(payload: dict[str, Any], *, added: bool) -> str:
        lines = [
            "Sub Added" if added else "Sub Removed",
            f"Guild ID: {payload['guild_id']}",
            f"Discord User ID: {payload['discord_user_id']}",
        ]
        if added:
            lines.append(f"Sub ID: {payload['sub_id']}")
            lines.append("Tracked Names: " + ", ".join(payload.get("send_names") or []))
        else:
            lines.append("Primary Send Name: " + (payload.get("send_name") or "(unknown)"))
        return "\n".join(lines)

    @staticmethod
    def _format_send_request_text(payload: dict[str, Any]) -> str:
        lines = [
            "Send Approval Requested",
            f"Request ID: {payload['request_id']}",
            f"Action: {payload['action']}",
            f"Status: {payload['status']}",
            f"Dom/me User ID: {payload['domme_user_id']}",
        ]
        if payload.get("target_send_id") is not None:
            lines.append(f"Target Send ID: {payload['target_send_id']}")
        lines.append("Next Step: the target Dom/me must approve this change in Discord.")
        return "\n".join(lines)

    @staticmethod
    def _format_block_text(payload: dict[str, Any]) -> str:
        return "\n".join(
            [
                "User Blocked" if payload.get("blocked") else "User Unblocked",
                f"Discord User ID: {payload['discord_user_id']}",
            ]
        )

    @staticmethod
    def _format_invalid_scan_options_text(payload: dict[str, Any]) -> str:
        return "\n".join(
            [
                "Guild Auto-Apply",
                "Invalid option list.",
                "Invalid: " + ", ".join(payload.get("invalid_options", [])),
                "Valid: " + ", ".join(payload.get("valid_options", [])),
            ]
        )

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
