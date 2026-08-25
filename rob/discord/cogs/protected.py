from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from rob.ui.cards.protected import protected_member_card
from rob.ui.render import add_card_actions

if TYPE_CHECKING:
    from rob.discord.client import RobBot

log = logging.getLogger(__name__)

# The memorial account this command celebrates (also in PROTECTED_USER_IDS).
_MEMORIAL_USER_ID = 1455563825393832095
_GOFUNDME_URL = "https://www.gofundme.com/f/help-lay-alyssa-rae-butler-to-rest"


class ProtectedCog(commands.Cog):
    """`!protected` posts the memorial card for the protected account."""

    def __init__(self, bot: "RobBot") -> None:
        self.bot = bot

    @commands.command(name="protected")
    async def protected(self, ctx: commands.Context) -> None:
        view = discord.ui.LayoutView(timeout=None)
        rendered = protected_member_card(
            user_id=_MEMORIAL_USER_ID,
            view=view,
        )
        add_card_actions(
            view,
            discord.ui.Button(
                label="Help Lay Alyssa Rae to Rest",
                url=_GOFUNDME_URL,
                style=discord.ButtonStyle.link,
            ),
        )
        try:
            await ctx.message.delete()
        except discord.HTTPException:
            log.debug("Could not delete the !protected command message.", exc_info=True)
        await ctx.send(
            **rendered.send_kwargs(),
            # Render the member's mention without pinging the account.
            allowed_mentions=discord.AllowedMentions.none(),
        )
