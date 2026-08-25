from __future__ import annotations

import re
from typing import TYPE_CHECKING

from discord.ext import commands

from rob.ui.cards.admin_tools import admin_permission_denied_card, admin_success_card, admin_usage_card

if TYPE_CHECKING:
    from rob.discord.client import RobBot

_MENTION_RE = re.compile(r"<@!?(\d+)>")


def _parse_user_id(target: str) -> int | None:
    raw = target.strip()
    mention = _MENTION_RE.fullmatch(raw)
    if mention:
        raw = mention.group(1)
    if not raw.isdigit():
        return None
    return int(raw)


class AdminToolsCog(commands.Cog):
    def __init__(self, bot: RobBot) -> None:
        self.bot = bot

    def _can_manage(self, ctx: commands.Context) -> bool:
        author = ctx.author
        if not hasattr(author, "guild_permissions"):
            return False
        return bool(author.guild_permissions.manage_guild)

    @commands.command(name="rob-blacklist")
    async def rob_blacklist(self, ctx: commands.Context, target: str, *, reason: str = "manual") -> None:
        if ctx.guild is None or not self._can_manage(ctx):
            await ctx.reply(**admin_permission_denied_card().send_kwargs())
            return
        discord_user_id = _parse_user_id(target)
        if discord_user_id is None:
            await ctx.reply(**admin_usage_card("!rob-blacklist <discord_user_id_or_mention> [reason]").send_kwargs())
            return
        await self.bot.blacklist_repo.add(
            discord_user_id=discord_user_id,
            reason=reason.strip() or "manual",
            created_by=ctx.author.id,
            guild_id=ctx.guild.id,
        )
        await ctx.reply(**admin_success_card(f"`{discord_user_id}` has been added to Rob's blacklist.").send_kwargs())

    @commands.command(name="rob-unblacklist")
    async def rob_unblacklist(self, ctx: commands.Context, target: str) -> None:
        if ctx.guild is None or not self._can_manage(ctx):
            await ctx.reply(**admin_permission_denied_card().send_kwargs())
            return
        discord_user_id = _parse_user_id(target)
        if discord_user_id is None:
            await ctx.reply(**admin_usage_card("!rob-unblacklist <discord_user_id_or_mention>").send_kwargs())
            return
        await self.bot.blacklist_repo.remove(discord_user_id=discord_user_id)
        await ctx.reply(**admin_success_card(f"`{discord_user_id}` has been removed from Rob's blacklist.").send_kwargs())

    @commands.command(name="throne-blacklist")
    async def throne_blacklist(self, ctx: commands.Context, target: str) -> None:
        if ctx.guild is None or not self._can_manage(ctx):
            await ctx.reply(**admin_permission_denied_card().send_kwargs())
            return
        discord_user_id = _parse_user_id(target)
        if discord_user_id is None:
            await ctx.reply(**admin_usage_card("!throne-blacklist <discord_user_id_or_mention>").send_kwargs())
            return

        await self.bot.dommes_repo.remove_by_user_id(ctx.guild.id, discord_user_id)
        await self.bot.blacklist_repo.add(
            discord_user_id=discord_user_id,
            reason="throne blacklist",
            created_by=ctx.author.id,
            guild_id=ctx.guild.id,
        )
        await ctx.reply(
            **admin_success_card(
                f"Removed Dom/me registration for `{discord_user_id}` and added to global blacklist."
            ).send_kwargs()
        )
