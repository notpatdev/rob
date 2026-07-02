from __future__ import annotations

import logging
import time
from datetime import timedelta
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from rob.config.guilds import MAIN_GUILD_ID, TEST_GUILD_ID
from rob.services.tldr_service import ChatMessage
from rob.ui.cards.errors import error_card
from rob.ui.cards.tldr import tldr_message
from rob.utils.time import utc_now

if TYPE_CHECKING:
    from rob.discord.client import RobBot

log = logging.getLogger(__name__)

# key -> (human label, lookback window)
_TIMEFRAMES: dict[str, tuple[str, timedelta]] = {
    "1h": ("the last hour", timedelta(hours=1)),
    "6h": ("the last 6 hours", timedelta(hours=6)),
    "24h": ("the last 24 hours", timedelta(hours=24)),
    "3d": ("the last 3 days", timedelta(days=3)),
    "7d": ("the last 7 days", timedelta(days=7)),
}
_DEFAULT_TIMEFRAME = "24h"

# Channel types Rob can read message history from for a summary.
_SUMMARISABLE = (discord.TextChannel, discord.Thread, discord.VoiceChannel)


class TldrCog(commands.Cog):
    def __init__(self, bot: "RobBot") -> None:
        self.bot = bot
        self._cooldowns: dict[int, float] = {}

    def _cooldown_remaining(self, user_id: int) -> int:
        expiry = self._cooldowns.get(user_id, 0.0)
        remaining = expiry - time.monotonic()
        return int(remaining) + 1 if remaining > 0 else 0

    def _mark_cooldown(self, user_id: int) -> None:
        seconds = self.bot.settings.tldr_cooldown_seconds
        if seconds <= 0:
            return
        now = time.monotonic()
        # Opportunistically drop expired entries so the map stays proportional to
        # currently-cooling-down users, not all-time invokers.
        expired = [uid for uid, expiry in self._cooldowns.items() if expiry <= now]
        for uid in expired:
            del self._cooldowns[uid]
        self._cooldowns[user_id] = now + seconds

    async def _collect_messages(
        self, channel, after
    ) -> list[ChatMessage]:
        limit = self.bot.settings.tldr_max_messages
        collected: list[ChatMessage] = []
        # Newest-first within the window, capped at the limit, so a very busy
        # channel yields the most *recent* messages rather than the oldest.
        async for message in channel.history(limit=limit, after=after, oldest_first=False):
            if message.author.bot:
                continue
            text = (message.clean_content or "").strip()
            if not text:
                continue
            collected.append(
                ChatMessage(
                    author=message.author.display_name,
                    content=text,
                    created_at=message.created_at,
                )
            )
        collected.reverse()  # restore chronological order
        return collected

    @app_commands.command(name="tldr", description="Summarise recent chat — by timeframe or topic.")
    @app_commands.guilds(MAIN_GUILD_ID, TEST_GUILD_ID)
    @app_commands.describe(
        timeframe="How far back to look (default: last 24 hours).",
        topic="Optional: focus the summary on a specific topic.",
        channel="Optional: summarise a different channel (default: this one).",
    )
    @app_commands.choices(
        timeframe=[
            app_commands.Choice(name="Last hour", value="1h"),
            app_commands.Choice(name="Last 6 hours", value="6h"),
            app_commands.Choice(name="Last 24 hours", value="24h"),
            app_commands.Choice(name="Last 3 days", value="3d"),
            app_commands.Choice(name="Last 7 days", value="7d"),
        ]
    )
    async def tldr(
        self,
        interaction: discord.Interaction,
        timeframe: app_commands.Choice[str] | None = None,
        topic: str | None = None,
        channel: discord.TextChannel | None = None,
    ) -> None:
        if not self.bot.settings.tldr_enabled:
            await interaction.response.send_message(
                **error_card("The TL;DR feature is turned off right now.").send_kwargs(),
                ephemeral=True,
            )
            return
        if interaction.guild is None:
            await interaction.response.send_message(
                **error_card("This command can only be used in a server.").send_kwargs(),
                ephemeral=True,
            )
            return

        remaining = self._cooldown_remaining(interaction.user.id)
        if remaining > 0:
            await interaction.response.send_message(
                **error_card(
                    f"Please wait {remaining}s before asking for another TL;DR."
                ).send_kwargs(),
                ephemeral=True,
            )
            return

        target = channel or interaction.channel
        if not isinstance(target, _SUMMARISABLE):
            await interaction.response.send_message(
                **error_card("Rob can only summarise text channels and threads.").send_kwargs(),
                ephemeral=True,
            )
            return

        member = (
            interaction.user
            if isinstance(interaction.user, discord.Member)
            else interaction.guild.get_member(interaction.user.id)
        )
        if member is None:
            await interaction.response.send_message(
                **error_card("Rob could not resolve your member record here.").send_kwargs(),
                ephemeral=True,
            )
            return

        # The requester must be able to read the channel they're summarising, and
        # so must Rob — never surface a channel the user can't already see.
        user_perms = target.permissions_for(member)
        if not (user_perms.view_channel and user_perms.read_message_history):
            await interaction.response.send_message(
                **error_card("You don't have access to read that channel.").send_kwargs(),
                ephemeral=True,
            )
            return
        me = target.guild.me
        bot_perms = target.permissions_for(me) if me is not None else None
        if bot_perms is None or not (bot_perms.view_channel and bot_perms.read_message_history):
            await interaction.response.send_message(
                **error_card("Rob can't read message history in that channel.").send_kwargs(),
                ephemeral=True,
            )
            return

        topic = (topic or "").strip() or None
        key = timeframe.value if timeframe is not None else _DEFAULT_TIMEFRAME
        label, delta = _TIMEFRAMES.get(key, _TIMEFRAMES[_DEFAULT_TIMEFRAME])
        after = utc_now() - delta

        # The summary is posted publicly, so the "thinking…" state is public too.
        await interaction.response.defer()
        self._mark_cooldown(interaction.user.id)

        try:
            messages = await self._collect_messages(target, after)
        except discord.Forbidden:
            await self._fail_after_defer(
                interaction, error_card("Rob isn't allowed to read that channel's history.")
            )
            return
        except discord.HTTPException:
            log.warning("Failed to fetch history for /tldr.", exc_info=True)
            await self._fail_after_defer(
                interaction,
                error_card("Rob couldn't fetch that channel's history right now."),
            )
            return

        try:
            result = await self.bot.tldr_service.summarize(
                messages,
                topic=topic,
                timeframe_label=label,
                channel_name=target.name,
            )
        except Exception:
            # The interaction is already deferred, so always give feedback rather
            # than leaving the user on a permanent "thinking…" state.
            log.exception("Failed to summarise chat for /tldr.")
            await self._fail_after_defer(
                interaction, error_card("Rob couldn't put together a summary right now.")
            )
            return

        content = tldr_message(
            channel_name=target.name,
            timeframe_label=label,
            summary=result.summary,
            method=result.method,
            message_count=result.message_count,
            participant_count=result.participant_count,
            topic=result.topic,
            matched_count=result.matched_count,
            model=result.model,
            ai_message_count=result.ai_message_count,
        )
        await interaction.followup.send(
            content=content,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _fail_after_defer(self, interaction: discord.Interaction, card) -> None:
        """Errors shouldn't be broadcast: remove the public "thinking…" message
        and tell only the requester what went wrong."""
        try:
            await interaction.delete_original_response()
        except discord.HTTPException:  # pragma: no cover - best-effort cleanup
            pass
        await interaction.followup.send(**card.send_kwargs(), ephemeral=True)
