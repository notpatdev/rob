from __future__ import annotations

import logging
from typing import Any

from aiohttp import web

from rob.opsapi._support import (
    _preview_handle_from_url,
)

log = logging.getLogger(__name__)


class DommeSubRoutes:

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
