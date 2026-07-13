from __future__ import annotations

import logging

from aiohttp import web


log = logging.getLogger(__name__)


class InactivityRoutes:

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
