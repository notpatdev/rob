from __future__ import annotations

import logging
import re
from collections import deque
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from rob.ui.cards.warn import warn_dm_card

if TYPE_CHECKING:
    from rob.discord.client import RobBot

log = logging.getLogger(__name__)

_WARN_TITLE_RE = re.compile(r"warn\s*\|\s*case", re.IGNORECASE)
_USER_MENTION_RE = re.compile(r"<@!?(\d+)>")
_WARNED_USER_FIELD_HINTS = ("offender", "warned", "user", "member", "target")
_MODERATOR_FIELD_HINTS = ("moderator", "mod", "staff", "issuer")
_MAX_PROCESSED_WARN_MESSAGES = 500


class WarnRelayCog(commands.Cog):
    def __init__(self, bot: RobBot) -> None:
        self.bot = bot
        self._processed_warn_message_ids: deque[int] = deque(maxlen=_MAX_PROCESSED_WARN_MESSAGES)

    def _extract_warned_user_id_from_embed(self, embed: discord.Embed) -> int | None:
        for field in embed.fields:
            name = (field.name or "").strip().lower()
            if not any(hint in name for hint in _WARNED_USER_FIELD_HINTS):
                continue
            if any(hint in name for hint in _MODERATOR_FIELD_HINTS):
                continue
            match = _USER_MENTION_RE.search(field.value or "")
            if match:
                return int(match.group(1))

        description = embed.description or ""
        mentions = [int(match.group(1)) for match in _USER_MENTION_RE.finditer(description)]
        if mentions:
            return mentions[0]
        return None

    async def _send_warn_dm(self, user_id: int, message_url: str) -> None:
        card = warn_dm_card(message_url)
        user = self.bot.get_user(user_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(user_id)
            except discord.HTTPException:
                user = None
        if user is None:
            return
        try:
            await user.send(**card.send_kwargs())
            log.info("Sent warn relay DM to user_id=%s", user_id)
        except discord.Forbidden:
            log.info("Could not DM warned user_id=%s (DMs closed).", user_id)
        except discord.HTTPException:
            log.warning("Failed to send warn relay DM to user_id=%s", user_id, exc_info=True)

    async def _process_carlbot_warn_message(self, message: discord.Message) -> None:
        if message.guild is None:
            return
        if await self.bot.maintenance_service.get_wind_down_phase() >= 1:
            return
        settings = await self.bot.guild_settings_repo.get(message.guild.id)
        if settings is None:
            return
        if settings.warn_log_channel_id is None or settings.carlbot_user_id is None:
            return
        if message.channel.id != settings.warn_log_channel_id:
            return
        if message.author.id != settings.carlbot_user_id:
            return
        if message.id in self._processed_warn_message_ids:
            return
        for embed in message.embeds:
            title = embed.title or ""
            if not _WARN_TITLE_RE.search(title):
                continue
            warned_user_id = self._extract_warned_user_id_from_embed(embed)
            if warned_user_id is None:
                log.warning(
                    "Carl-bot warn detected in message_id=%s but warned user could not be extracted.",
                    message.id,
                )
                continue
            self._processed_warn_message_ids.append(message.id)
            await self._send_warn_dm(warned_user_id, message.jump_url)
            return

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        await self._process_carlbot_warn_message(message)

    @commands.Cog.listener()
    async def on_message_edit(self, _before: discord.Message, after: discord.Message) -> None:
        await self._process_carlbot_warn_message(after)
