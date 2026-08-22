from __future__ import annotations

import asyncio
import hashlib
import logging
from contextlib import suppress
from typing import Protocol, cast

import discord

from bill.embeds import build_send_embed, send_footer_id
from bill.worker_client import SendNotification, WorkerAPIError, WorkerClient

log = logging.getLogger("bill.notifications")


class BillClient(Protocol):
    def get_channel(self, channel_id: int) -> discord.abc.GuildChannel | discord.Thread | None: ...

    async def wait_until_ready(self) -> None: ...


def notification_nonce(send_id: str) -> int:
    digest = hashlib.sha256(f"bill-send:{send_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)


class NotificationPoller:
    def __init__(
        self,
        *,
        bot: BillClient,
        worker: WorkerClient,
        owner: str,
        interval_seconds: int,
        batch_size: int,
        lease_seconds: int,
    ) -> None:
        self._bot = bot
        self._worker = worker
        self._owner = owner
        self._interval_seconds = interval_seconds
        self._batch_size = batch_size
        self._lease_seconds = lease_seconds
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="bill-notification-poller")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def poll_once(self) -> None:
        notifications = await self._worker.lease_notifications(
            owner=self._owner,
            limit=self._batch_size,
            lease_seconds=self._lease_seconds,
        )
        for notification in notifications:
            await self._deliver(notification)

    async def _run(self) -> None:
        await self._bot.wait_until_ready()
        while True:
            try:
                await self.poll_once()
            except WorkerAPIError as exc:
                log.warning("Notification lease failed: %s", exc)
            except Exception:
                log.exception("Unexpected notification polling failure")
            await asyncio.sleep(self._interval_seconds)

    async def _deliver(self, notification: SendNotification) -> None:
        channel = self._bot.get_channel(int(notification.channel_id))
        if not isinstance(channel, discord.TextChannel):
            await self._nack(notification, "Configured send channel is unavailable", permanent=True)
            return

        try:
            existing = (
                await self._find_existing(channel, notification.send_id)
                if notification.delivery_may_exist
                else None
            )
            message = existing or await channel.send(
                embed=build_send_embed(notification),
                nonce=notification_nonce(notification.send_id),
            )
            await self._worker.ack_notification(
                notification.notification_id,
                lease_token=notification.lease_token,
                discord_message_id=message.id,
            )
        except (discord.Forbidden, discord.NotFound) as exc:
            await self._nack(notification, type(exc).__name__, permanent=True)
        except (discord.HTTPException, WorkerAPIError) as exc:
            await self._nack(notification, type(exc).__name__, permanent=False)

    async def _nack(
        self,
        notification: SendNotification,
        error: str,
        *,
        permanent: bool,
    ) -> None:
        try:
            await self._worker.nack_notification(
                notification.notification_id,
                lease_token=notification.lease_token,
                error=error,
                permanent=permanent,
            )
        except WorkerAPIError:
            log.exception(
                "Failed to nack notification notification_id=%s",
                notification.notification_id,
            )

    @staticmethod
    async def _find_existing(
        channel: discord.TextChannel,
        send_id: str,
    ) -> discord.Message | None:
        marker = send_footer_id(send_id)
        async for message in channel.history(limit=500):
            if message.author.id != channel.guild.me.id:
                continue
            if any(embed.footer and embed.footer.text == marker for embed in message.embeds):
                return cast(discord.Message, message)
        return None
