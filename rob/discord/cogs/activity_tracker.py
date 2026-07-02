from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from rob.config.guilds import is_new_system_guild

if TYPE_CHECKING:
    from rob.discord.client import RobBot

log = logging.getLogger(__name__)


class ActivityTrackerCog(commands.Cog):
    """Records member activity (messages, reactions, interactions) so the
    inactivity system can tell active members from inactive ones, and reactivates
    a member the instant they interact again.

    Runs on the main + test guilds (the "new system" guilds)."""

    def __init__(self, bot: "RobBot") -> None:
        self.bot = bot

    def _service(self):
        return getattr(self.bot, "inactivity_service", None)

    async def _register(
        self,
        guild: discord.Guild | None,
        member: discord.abc.User | None,
        *,
        channel_id: int | None = None,
        thread_parent_id: int | None = None,
    ) -> None:
        if guild is None or not is_new_system_guild(guild.id):
            return
        if member is None or getattr(member, "bot", False):
            return
        service = self._service()
        if service is None:
            return
        try:
            await service.register_member_activity(
                guild,
                member,
                channel_id=channel_id,
                thread_parent_id=thread_parent_id,
            )
        except Exception:  # pragma: no cover - never let tracking break event handling
            log.exception(
                "Failed to record activity for user_id=%s guild_id=%s",
                getattr(member, "id", None),
                guild.id,
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        await self._register(
            message.guild,
            message.author,
            channel_id=getattr(message.channel, "id", None),
            thread_parent_id=getattr(message.channel, "parent_id", None),
        )

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.guild_id is None:
            return
        channel = self.bot.get_channel(payload.channel_id)
        thread_parent_id = getattr(channel, "parent_id", None)
        if payload.member is not None:
            guild = self.bot.get_guild(payload.guild_id) or payload.member.guild
            await self._register(
                guild,
                payload.member,
                channel_id=payload.channel_id,
                thread_parent_id=thread_parent_id,
            )
        elif payload.user_id and is_new_system_guild(payload.guild_id):
            # Uncached reactor: we can't check their roles here, so only record
            # the raw activity signal when the channel would count for
            # reactivation — otherwise an inactive member could earn credit
            # outside the main channel and get flipped back at the next sweep.
            service = self._service()
            if service is not None and service.counts_for_reactivation(
                payload.channel_id, thread_parent_id
            ):
                try:
                    await service.record_activity(payload.guild_id, payload.user_id)
                except Exception:  # pragma: no cover - defensive
                    log.exception(
                        "Failed to record reaction activity user_id=%s guild_id=%s",
                        payload.user_id,
                        payload.guild_id,
                    )

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        await self._register(
            interaction.guild,
            member,
            channel_id=getattr(interaction, "channel_id", None),
            thread_parent_id=getattr(interaction.channel, "parent_id", None),
        )
