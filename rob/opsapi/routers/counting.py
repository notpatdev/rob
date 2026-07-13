from __future__ import annotations

import logging
from typing import Any

from aiohttp import web


log = logging.getLogger(__name__)


class CountingRoutes:

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
