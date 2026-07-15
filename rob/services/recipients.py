"""Shared audience for Rob's farewell messages.

Both ``/shutdown`` (the announcement) and the 1 August final sequence DM the
*same* people — every registered Dom/me and Sub in the guild, de-duplicated
(Dom/mes first) and minus blacklisted users. Keeping this in one place means the
two can never drift apart.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

if TYPE_CHECKING:
    from rob.discord.client import RobBot

log = logging.getLogger(__name__)


async def resolve_registered_recipient_ids(bot: "RobBot", guild_id: int) -> list[int]:
    """Distinct discord ids of registered Dom/mes + Subs, minus blacklisted.

    De-duplicated across the two tables and preserving Dom/mes-first order.
    """
    dommes = await bot.dommes_repo.list_for_guild(guild_id)
    subs = await bot.subs_repo.list_for_guild(guild_id)

    ids: list[int] = []
    seen: set[int] = set()
    for entry in [*dommes, *subs]:
        user_id = int(entry.discord_user_id)
        if user_id in seen:
            continue
        seen.add(user_id)
        if await bot.blacklist_repo.contains(user_id):
            continue
        ids.append(user_id)
    return ids


async def resolve_messageable_recipients(
    bot: "RobBot", user_ids: list[int]
) -> list[discord.abc.Messageable]:
    """Resolve ids to messageable users, skipping any that can't be found."""
    recipients: list[discord.abc.Messageable] = []
    for user_id in user_ids:
        user = bot.get_user(user_id)
        if user is None:
            try:
                user = await bot.fetch_user(user_id)
            except discord.HTTPException:
                log.warning("Farewell: could not resolve user_id=%s", user_id)
                continue
        recipients.append(user)
    return recipients
