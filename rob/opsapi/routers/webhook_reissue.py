from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from aiohttp import web

from rob.utils.money import format_money_from_cents
from rob.opsapi._support import (
    WEBHOOK_REISSUE_SENT_PREFIX,
)

log = logging.getLogger(__name__)


class WebhookReissueRoutes:

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

    @staticmethod
    def _webhook_reissue_sent_key(guild_id: int, discord_user_id: int) -> str:
        return f"{WEBHOOK_REISSUE_SENT_PREFIX}:{guild_id}:{discord_user_id}"

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
