from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

from rob.opsapi._support import SupportMixin

log = logging.getLogger(__name__)


class MaintenanceRoutes:

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

    @staticmethod
    def _format_maintenance_text(payload: dict[str, Any]) -> str:
        reason = SupportMixin._display_text(payload.get("reason")) or "(none)"
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
    def _format_block_text(payload: dict[str, Any]) -> str:
        return "\n".join(
            [
                "User Blocked" if payload.get("blocked") else "User Unblocked",
                f"Discord User ID: {payload['discord_user_id']}",
            ]
        )
