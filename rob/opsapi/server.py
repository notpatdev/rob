from __future__ import annotations

import logging

import discord
from aiohttp import web

from rob.opsapi._support import SupportMixin
from rob.opsapi.routers.guilds import GuildRoutes
from rob.opsapi.routers.sends import SendRoutes
from rob.opsapi.routers.maintenance import MaintenanceRoutes
from rob.opsapi.routers.counting import CountingRoutes
from rob.opsapi.routers.inactivity import InactivityRoutes
from rob.opsapi.routers.backups import BackupRoutes
from rob.opsapi.routers.dommes_subs import DommeSubRoutes
from rob.opsapi.routers.webhook_reissue import WebhookReissueRoutes

log = logging.getLogger(__name__)


class BotOpsServer(GuildRoutes, SendRoutes, MaintenanceRoutes, CountingRoutes, InactivityRoutes, BackupRoutes, DommeSubRoutes, WebhookReissueRoutes, SupportMixin):

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

    async def _handle_health(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)
        return web.json_response({"ok": True, "bot_user_id": getattr(self.bot.user, "id", None)})
