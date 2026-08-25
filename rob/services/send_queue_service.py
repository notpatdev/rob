from __future__ import annotations

import asyncio
import inspect
import logging

import discord

from rob.database.repositories.dommes import DommesRepository
from rob.database.repositories.guild_settings import GuildSettingsRepository
from rob.database.repositories.leaderboards import LeaderboardsRepository
from rob.database.repositories.sends import SendsRepository
from rob.services.counting_service import CountingService
from rob.services.leaderboard_service import LeaderboardService
from rob.services.maintenance_service import MaintenanceService
from rob.services.send_display import build_sub_display
from rob.ui.cards.send import send_card

log = logging.getLogger(__name__)


class SendQueueService:
    def __init__(
        self,
        *,
        bot: discord.Client,
        sends: SendsRepository,
        guild_settings: GuildSettingsRepository,
        maintenance: MaintenanceService,
        leaderboard_service: LeaderboardService,
        counting_service: CountingService | None = None,
        leaderboards: LeaderboardsRepository | None = None,
        dommes: DommesRepository | None = None,
        include_test_sends: bool = False,
        owner_test_user_id: int | None = None,
        test_gifter_usernames: tuple[str, ...] = (),
        poll_interval_seconds: float = 5.0,
    ) -> None:
        self.bot = bot
        self.sends = sends
        self.guild_settings = guild_settings
        self.maintenance = maintenance
        self.leaderboard_service = leaderboard_service
        self.counting_service = counting_service
        self.leaderboards = leaderboards
        self.dommes = dommes
        self.include_test_sends = include_test_sends
        self.owner_test_user_id = owner_test_user_id
        self.test_gifter_usernames = test_gifter_usernames
        self.poll_interval_seconds = poll_interval_seconds
        self._task: asyncio.Task[None] | None = None
        self._stopping = False
        self._startup_leaderboard_refresh_done = False
        self._send_notifications: asyncio.Queue[int] | None = None

    async def _maintenance_bool(self, method_name: str, guild_id: int | None = None) -> bool:
        method = getattr(self.maintenance, method_name, None)
        if method is None:
            return False
        result = method(guild_id) if guild_id is not None else method()
        if inspect.isawaitable(result):
            return bool(await result)
        if isinstance(result, bool):
            return result
        return False

    async def _send_tracking_disabled_for_guild(self, guild_id: int | None) -> bool:
        return await self._maintenance_bool("send_tracking_disabled_for_guild", guild_id)

    async def _count_recovery_disabled_for_guild(self, guild_id: int | None) -> bool:
        return await self._maintenance_bool("count_recovery_disabled_for_guild", guild_id)

    async def _rob_offline_for_guild(self, guild_id: int | None) -> bool:
        return await self._maintenance_bool("is_rob_offline_for_guild", guild_id)

    def _notification_queue(self) -> asyncio.Queue[int]:
        if self._send_notifications is None:
            self._send_notifications = asyncio.Queue()
        return self._send_notifications

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stopping = False
        self._task = asyncio.create_task(self._run(), name="rob-send-queue")

    async def stop(self) -> None:
        self._stopping = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _run(self) -> None:
        await self.bot.wait_until_ready()
        await self._refresh_leaderboards_on_startup()
        try:
            # Drain sends stranded in 'pending' by a crash or missed notify.
            await self.process_cycle()
        except Exception:
            log.exception("Startup pending-send drain failed.")
        while not self._stopping:
            try:
                try:
                    send_id = await asyncio.wait_for(
                        self._notification_queue().get(),
                        timeout=self.poll_interval_seconds,
                    )
                except TimeoutError:
                    await self.process_idle_tasks()
                else:
                    await self.process_send_by_id(send_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Send queue cycle failed.")

    async def _refresh_leaderboards_on_startup(self) -> None:
        if self._startup_leaderboard_refresh_done:
            return
        self._startup_leaderboard_refresh_done = True
        try:
            log.info("Refreshing all leaderboard messages on bot startup.")
            await self.leaderboard_service.refresh_all_guilds()
        except Exception:
            log.exception("Startup leaderboard refresh failed.")

    async def process_cycle(self) -> None:
        if not await self.maintenance.is_enabled():
            released = await self.sends.release_queued_maintenance()
            if released:
                log.info("Released %s queued maintenance send(s).", released)

        pending = await self.sends.fetch_for_status("pending", limit=50)
        if pending:
            log.info("Send queue cycle: %s pending send(s).", len(pending))
        if self.counting_service is not None:
            queued_maintenance = await self.sends.fetch_for_status("queued_maintenance", limit=50)
            recovery_candidates = list(pending) + list(queued_maintenance)
            for send in recovery_candidates:
                if await self._count_recovery_disabled_for_guild(send.guild_id):
                    continue
                try:
                    await self.counting_service.process_send_for_count_rescue(send)
                except Exception:
                    log.exception(
                        "Count rescue evaluation failed for send_id=%s guild_id=%s.",
                        send.id,
                        send.guild_id,
                    )

        if not await self.maintenance.is_enabled():
            for send in pending:
                ok = await self._post_send(send)
                if ok:
                    log.info("Posted send id=%s guild_id=%s", send.id, send.guild_id)
                    if await self._rob_offline_for_guild(send.guild_id):
                        continue
                    try:
                        log.info("Refreshing leaderboard for guild_id=%s", send.guild_id)
                        await self.leaderboard_service.refresh_guild(send.guild_id)
                    except Exception:
                        log.exception(
                            "Leaderboard refresh failed after posted send_id=%s guild_id=%s.",
                            send.id,
                            send.guild_id,
                        )

        if not await self.maintenance.is_enabled():
            if await self.maintenance.consume_leaderboard_refresh_request():
                await self.leaderboard_service.refresh_all_guilds()

    async def process_idle_tasks(self) -> None:
        """Delivery is notify-driven; this idle sweep is the backstop that
        keeps a dropped notify or a restart from stranding a pending send."""
        await self.process_cycle()

    async def notify_send(self, send_id: int) -> None:
        self._notification_queue().put_nowait(int(send_id))

    async def process_send_by_id(self, send_id: int) -> bool:
        send = await self.sends.get(int(send_id))
        if send is None:
            log.warning("Send notification ignored because send_id=%s was not found.", send_id)
            return False
        return await self._process_send_record(send)

    async def refresh_send_message(
        self,
        *,
        send_id: int,
        message_id: int,
        adjustment_note: str | None = None,
    ) -> bool:
        send = await self.sends.get(int(send_id))
        if send is None:
            return False

        settings = await self.guild_settings.get(send.guild_id)
        if settings is None or settings.send_track_channel_id is None:
            return False

        guild = self.bot.get_guild(send.guild_id)
        if guild is None:
            return False

        channel = guild.get_channel(settings.send_track_channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(settings.send_track_channel_id)
            except (discord.NotFound, discord.HTTPException):
                return False

        if not isinstance(channel, discord.TextChannel):
            return False

        try:
            message = await channel.fetch_message(message_id)
            throne_url = await self._resolve_throne_url(send)
            msg = send_card(
                send=send,
                domme_label=f"<@{send.domme_user_id}>",
                sub_display=build_sub_display(
                    send,
                    test_gifter_usernames=self.test_gifter_usernames,
                ),
                adjustment_note=adjustment_note,
                throne_url=throne_url,
            )
            await message.edit(**msg.edit_kwargs())
        except (discord.NotFound, discord.HTTPException):
            return False
        return True

    async def _resolve_throne_url(self, send) -> str | None:
        if self.dommes is None:
            return None
        domme = await self.dommes.get_by_user_id(int(send.guild_id), int(send.domme_user_id))
        return getattr(domme, "throne_url", None) if domme is not None else None

    async def _process_send_record(self, send) -> bool:
        if self.counting_service is not None and not await self._count_recovery_disabled_for_guild(send.guild_id):
            try:
                await self.counting_service.process_send_for_count_rescue(send)
            except Exception:
                log.exception(
                    "Count rescue evaluation failed for send_id=%s guild_id=%s.",
                    send.id,
                    send.guild_id,
                )

        if send.discord_post_status == "queued_maintenance":
            if await self.maintenance.is_enabled():
                log.info(
                    "Send id=%s guild_id=%s is queued until maintenance ends.",
                    send.id,
                    send.guild_id,
                )
                return False
            released = await self.sends.release_queued_maintenance()
            if released:
                log.info("Released %s queued maintenance send(s).", released)
            send = await self.sends.get(send.id)
            if send is None:
                return False

        if send.discord_post_status != "pending":
            log.info(
                "Send notification ignored for send_id=%s guild_id=%s status=%s.",
                send.id,
                send.guild_id,
                send.discord_post_status,
            )
            return False

        ok = await self._post_send(send)
        if ok:
            log.info("Posted send id=%s guild_id=%s", send.id, send.guild_id)
            if await self._rob_offline_for_guild(send.guild_id):
                return ok
            try:
                log.info("Refreshing leaderboard for guild_id=%s", send.guild_id)
                await self.leaderboard_service.refresh_guild(send.guild_id)
            except Exception:
                log.exception(
                    "Leaderboard refresh failed after posted send_id=%s guild_id=%s.",
                    send.id,
                    send.guild_id,
                )
        return ok

    async def _post_send(self, send) -> bool:
        if await self.maintenance.get_wind_down_phase() >= 1:
            log.info(
                "Send id=%s guild_id=%s saved without Discord notification (wind-down).",
                send.id,
                send.guild_id,
            )
            await self.sends.mark_posted(send.id, message_id=None)
            return True

        if await self._send_tracking_disabled_for_guild(send.guild_id):
            log.info(
                "Send id=%s guild_id=%s saved without Discord notification while Rob is offline.",
                send.id,
                send.guild_id,
            )
            await self.sends.mark_posted(send.id, message_id=None)
            return True

        if await self.maintenance.is_enabled():
            log.info(
                "Send id=%s guild_id=%s held during maintenance window.",
                send.id,
                send.guild_id,
            )
            await self.sends.update_status(send.id, "queued_maintenance")
            return False

        settings = await self.guild_settings.get(send.guild_id)
        if settings is None or settings.send_track_channel_id is None:
            await self.sends.mark_failed(send.id, error="Missing send tracking channel configuration.")
            return False

        guild = self.bot.get_guild(send.guild_id)
        if guild is None:
            await self.sends.mark_failed(send.id, error="Guild not available to bot.")
            return False

        channel = guild.get_channel(settings.send_track_channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(settings.send_track_channel_id)
            except (discord.NotFound, discord.HTTPException) as exc:
                await self.sends.mark_failed(send.id, error=f"Send tracking channel unavailable: {exc}")
                return False

        if not isinstance(channel, discord.TextChannel):
            await self.sends.mark_failed(send.id, error="Configured send tracking channel is not a text channel.")
            return False

        try:
            throne_url = await self._resolve_throne_url(send)
            msg = send_card(
                send=send,
                domme_label=f"<@{send.domme_user_id}>",
                sub_display=build_sub_display(
                    send,
                    test_gifter_usernames=self.test_gifter_usernames,
                ),
                throne_url=throne_url,
            )
            message = await channel.send(**msg.send_kwargs())
        except discord.HTTPException as exc:
            await self.sends.mark_failed(send.id, error=f"Discord post failed: {exc}")
            return False

        await self.sends.mark_posted(send.id, message_id=message.id)
        log.info("Marked send posted id=%s message_id=%s", send.id, message.id)
        return True
