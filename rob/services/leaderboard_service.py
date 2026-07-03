from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging

import discord

from rob.config.guilds import is_new_system_guild
from rob.database.repositories.bot_state import BotStateRepository
from rob.database.repositories.guild_settings import GuildSettingsRepository
from rob.database.repositories.leaderboards import LeaderboardsRepository
from rob.database.repositories.models import LeaderboardEntry, LeaderboardSummary
from rob.ui.cards.leaderboard import leaderboard_card, leaderboard_stats_card
from rob.ui.cards.maintenance import rob_offline_embed
from rob.services.leaderboard_status import LeaderboardStatus
from rob.services.maintenance_service import MaintenanceService

log = logging.getLogger(__name__)


def _compute_content_hash(
    entries: list[LeaderboardEntry],
    summary: LeaderboardSummary,
    maintenance_enabled: bool,
) -> str:
    data = {
        "entries": [(e.label, e.total_cents, e.send_count) for e in entries],
        "summary": {
            "domme_count": summary.domme_count,
            "send_count": summary.send_count,
            "sub_count": summary.sub_count,
            "total_cents": summary.total_cents,
            "unclaimed_send_count": summary.unclaimed_send_count,
            "unclaimed_total_cents": summary.unclaimed_total_cents,
        },
        "maintenance": maintenance_enabled,
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


class LeaderboardService:
    def __init__(
        self,
        *,
        bot: discord.Client,
        guild_settings: GuildSettingsRepository,
        leaderboards: LeaderboardsRepository,
        bot_state: BotStateRepository,
        maintenance: MaintenanceService,
        dommes=None,
        leaderboard_limit: int = 10,
        include_test_sends: bool = False,
        test_gifter_usernames: tuple[str, ...] = (),
        owner_test_user_id: int | None = None,
    ) -> None:
        self.bot = bot
        self.guild_settings = guild_settings
        self.leaderboards = leaderboards
        self.bot_state = bot_state
        self.maintenance = maintenance
        self.dommes = dommes
        self.leaderboard_limit = leaderboard_limit
        self.include_test_sends = include_test_sends
        self.test_gifter_usernames = test_gifter_usernames
        self.owner_test_user_id = owner_test_user_id
        self._refresh_locks: dict[int, asyncio.Lock] = {}
        self._content_hashes: dict[int, str] = {}

    async def _rob_offline_for_guild(self, guild_id: int | None) -> bool:
        checker = getattr(self.maintenance, "is_rob_offline_for_guild", None)
        if checker is None:
            return False
        result = checker(guild_id)
        if inspect.isawaitable(result):
            return bool(await result)
        if isinstance(result, bool):
            return result
        return False

    async def _leaderboard_status_for_guild(self, guild_id: int | None):
        checker = getattr(self.maintenance, "get_leaderboard_status", None)
        if checker is None:
            return LeaderboardStatus.MAINTENANCE if await self.maintenance.is_enabled() else LeaderboardStatus.LIVE
        try:
            result = checker(guild_id)
        except TypeError:
            result = checker()
        if inspect.isawaitable(result):
            return await result
        return result

    async def _filter_entries_for_guild(
        self,
        guild_id: int,
        entries: list[LeaderboardEntry],
    ) -> list[LeaderboardEntry]:
        """Pass-through: the leaderboard-visibility opt-out was removed. Kept
        as a hook for future per-guild filtering."""
        del guild_id
        return entries

    async def refresh_all_guilds(self) -> None:
        for guild_id in await self.guild_settings.list_guild_ids():
            await self.refresh_guild(guild_id)

    async def refresh_guild(self, guild_id: int) -> bool:
        lock = self._refresh_locks.setdefault(guild_id, asyncio.Lock())
        if lock.locked():
            log.info("Leaderboard sync skipped; another sync is already running for guild_id=%s", guild_id)
            return False

        async with lock:
            log.info("Leaderboard sync started for guild_id=%s", guild_id)
            settings = await self.guild_settings.get(guild_id)
            if settings is None or settings.leaderboard_channel_id is None:
                log.warning(
                    "Skipping leaderboard refresh for guild_id=%s: leaderboard channel not configured.",
                    guild_id,
                )
                return False

            guild = self.bot.get_guild(guild_id)
            if guild is None:
                log.warning("Guild %s is not available for leaderboard refresh.", guild_id)
                return False

            channel = guild.get_channel(settings.leaderboard_channel_id)
            if channel is None:
                try:
                    channel = await guild.fetch_channel(settings.leaderboard_channel_id)
                except (discord.NotFound, discord.HTTPException):
                    log.warning(
                        "Leaderboard channel %s is unavailable in guild %s.",
                        settings.leaderboard_channel_id,
                        guild_id,
                    )
                    return False
            if not isinstance(channel, discord.TextChannel):
                log.warning(
                    "Skipping leaderboard refresh for guild_id=%s: leaderboard channel %s is not a text channel.",
                    guild_id,
                    settings.leaderboard_channel_id,
                )
                return False
            summary = await self.leaderboards.get_summary(
                guild_id,
                include_test_sends=self.include_test_sends,
                test_gifter_usernames=self.test_gifter_usernames,
                owner_test_user_id=self.owner_test_user_id,
            )
            log.info(
                "Registered Dom/mes loaded=%s guild_id=%s",
                summary.domme_count,
                guild_id,
            )
            dommes = await self.leaderboards.get_top_dommes(
                guild_id,
                limit=min(self.leaderboard_limit, 10),
                include_test_sends=self.include_test_sends,
                test_gifter_usernames=self.test_gifter_usernames,
                owner_test_user_id=self.owner_test_user_id,
            )
            dommes = await self._filter_entries_for_guild(guild_id, dommes)
            log.info(
                "Leaderboard entries rendered=%s leaderboard_limit=%s guild_id=%s",
                len(dommes),
                min(self.leaderboard_limit, 10),
                guild_id,
            )
            maintenance_enabled = await self.maintenance.is_enabled()
            rob_offline_enabled = await self._rob_offline_for_guild(guild_id)
            content_hash = _compute_content_hash(dommes, summary, maintenance_enabled or rob_offline_enabled)
            if self._content_hashes.get(guild_id) == content_hash:
                log.info("Leaderboard content unchanged; skipping Discord edits guild_id=%s", guild_id)
                return True
            self._content_hashes[guild_id] = content_hash

            if rob_offline_enabled:
                dommes_msg = rob_offline_embed()
                stats_msg = rob_offline_embed()
            else:
                dommes_msg = leaderboard_card(
                    title="ignored",
                    entries=dommes,
                    summary=summary,
                    status=await self._leaderboard_status_for_guild(guild_id),
                )
                stats_msg = leaderboard_stats_card(
                    summary,
                    dommes,
                    maintenance_enabled=maintenance_enabled,
                )

            main_ok = await self._ensure_message(
                guild_id=guild_id,
                channel=channel,
                message_key="leaderboard",
                leaderboard_type="discord",
                rendered=dommes_msg,
            )
            stats_ok = await self._ensure_message(
                guild_id=guild_id,
                channel=channel,
                message_key="leaderboard_stats",
                leaderboard_type="discord",
                rendered=stats_msg,
            )
            return main_ok and stats_ok

    async def get_current_leader(self, guild_id: int):
        leader = await self.leaderboards.get_current_leader(
            guild_id,
            include_test_sends=self.include_test_sends,
            test_gifter_usernames=self.test_gifter_usernames,
            owner_test_user_id=self.owner_test_user_id,
        )
        if leader is None:
            return None
        if leader.total_cents <= 0 and leader.send_count <= 0:
            return None
        if is_new_system_guild(guild_id):
            filtered = await self._filter_entries_for_guild(guild_id, [leader])
            if not filtered:
                return None
            leader = filtered[0]
        return leader

    async def _create_and_save_message(
        self,
        *,
        guild_id: int,
        channel: discord.TextChannel,
        message_key: str,
        leaderboard_type: str,
        rendered,
    ) -> bool:
        message = await channel.send(**rendered.send_kwargs())
        await self._upsert_ref(
            guild_id=guild_id,
            message_key=message_key,
            leaderboard_type=leaderboard_type,
            channel_id=channel.id,
            message_id=message.id,
        )
        log.info(
            "Leaderboard message ID saved guild_id=%s key=%s channel_id=%s message_id=%s",
            guild_id,
            message_key,
            channel.id,
            message.id,
        )
        return True

    async def _ensure_message(
        self,
        *,
        guild_id: int,
        channel: discord.TextChannel,
        message_key: str,
        leaderboard_type: str,
        rendered,
    ) -> bool:
        ref = await self.leaderboards.get_message(guild_id, message_key)
        if ref is None:
            log.info(
                "No leaderboard message configured; creating initial message guild_id=%s key=%s",
                guild_id,
                message_key,
            )
            return await self._create_and_save_message(
                guild_id=guild_id,
                channel=channel,
                message_key=message_key,
                leaderboard_type=leaderboard_type,
                rendered=rendered,
            )

        log.info(
            "Loaded leaderboard config guild_id=%s key=%s channel_id=%s message_id=%s",
            guild_id,
            message_key,
            ref.channel_id,
            ref.message_id,
        )
        if ref.channel_id != channel.id:
            log.info(
                "Leaderboard ref channel mismatch for guild_id=%s key=%s ref_channel_id=%s configured_channel_id=%s. Creating replacement in configured channel.",
                guild_id,
                message_key,
                ref.channel_id,
                channel.id,
            )
            return await self._create_and_save_message(
                guild_id=guild_id,
                channel=channel,
                message_key=message_key,
                leaderboard_type=leaderboard_type,
                rendered=rendered,
            )

        message = channel.get_partial_message(ref.message_id)
        try:
            await message.edit(**rendered.edit_kwargs())
            log.info(
                "Found existing leaderboard message; edited in place guild_id=%s key=%s message_id=%s",
                guild_id,
                message_key,
                ref.message_id,
            )
            return True
        except (discord.NotFound, KeyError):
            log.info(
                "Leaderboard message missing; creating one replacement guild_id=%s key=%s channel_id=%s message_id=%s",
                guild_id,
                message_key,
                ref.channel_id,
                ref.message_id,
            )
            return await self._create_and_save_message(
                guild_id=guild_id,
                channel=channel,
                message_key=message_key,
                leaderboard_type=leaderboard_type,
                rendered=rendered,
            )
        except discord.Forbidden:
            log.warning(
                "Leaderboard sync could not edit existing message due to permissions guild_id=%s key=%s channel_id=%s message_id=%s",
                guild_id,
                message_key,
                ref.channel_id,
                ref.message_id,
            )
            return False
        except discord.HTTPException:
            log.exception(
                "Leaderboard sync failed to edit existing message guild_id=%s key=%s channel_id=%s message_id=%s",
                guild_id,
                message_key,
                ref.channel_id,
                ref.message_id,
            )
            return False

    async def _upsert_ref(
        self,
        *,
        guild_id: int,
        message_key: str,
        leaderboard_type: str,
        channel_id: int,
        message_id: int,
    ) -> None:
        await self.leaderboards.upsert_message(
            guild_id=guild_id,
            message_key=message_key,
            leaderboard_type=leaderboard_type,
            channel_id=channel_id,
            message_id=message_id,
        )
