from __future__ import annotations

import logging
import socket
import uuid

import aiohttp
import discord
from discord.ext import commands

from bill.cogs.registration import RegistrationCog
from bill.cogs.setup import BillSetupCog
from bill.notifications import NotificationPoller
from bill.settings import Settings
from bill.worker_client import WorkerClient

log = logging.getLogger("bill")


class BillBot(commands.Bot):
    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.settings = settings
        self.http_session: aiohttp.ClientSession | None = None
        self.worker: WorkerClient | None = None
        self.poller: NotificationPoller | None = None

    async def setup_hook(self) -> None:
        self.http_session = aiohttp.ClientSession()
        self.worker = WorkerClient(
            base_url=self.settings.worker_base_url,
            api_token=self.settings.worker_api_token,
            session=self.http_session,
        )
        await self.add_cog(BillSetupCog(self))
        await self.add_cog(RegistrationCog(self))

        if self.settings.test_guild_id is not None:
            guild = discord.Object(id=self.settings.test_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()

        owner = f"{socket.gethostname()}:{uuid.uuid4().hex}"
        self.poller = NotificationPoller(
            bot=self,
            worker=self.worker,
            owner=owner,
            interval_seconds=self.settings.poll_interval_seconds,
            batch_size=self.settings.notification_batch_size,
            lease_seconds=self.settings.notification_lease_seconds,
        )
        self.poller.start()

    async def close(self) -> None:
        if self.poller is not None:
            await self.poller.stop()
        if self.http_session is not None:
            await self.http_session.close()
        await super().close()

    async def on_ready(self) -> None:
        log.info("Bill is ready as %s in %d guilds", self.user, len(self.guilds))

    def require_worker(self) -> WorkerClient:
        if self.worker is None:
            raise RuntimeError("Worker client is not initialized")
        return self.worker
