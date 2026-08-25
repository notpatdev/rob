from __future__ import annotations

import logging
from typing import Any

from aiohttp import web


log = logging.getLogger(__name__)


class BackupRoutes:

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
