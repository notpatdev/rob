from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord.ext import commands


DENY_TITLE = "That didn't work"
DENY_BODY = (
    "Hi!\n\n"
    "You're receiving this message due to one of the following reasons:\n\n"
    "• Blacklist of services from Rob\n"
    "• A temporary server error\n"
    "• A command or feature being retired\n\n"
    "Please contact the bot developer if you continue to see this issue."
)


def build_deny_embed() -> discord.Embed:
    embed = discord.Embed(
        title=DENY_TITLE,
        description=DENY_BODY,
        color=discord.Color.red(),
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text="Rob")
    return embed


async def send_deny_response(target) -> None:
    """Send the generic deny card. Never reveals blacklist status."""
    embed = build_deny_embed()

    if isinstance(target, discord.Interaction):
        try:
            if target.response.is_done():
                await target.followup.send(embed=embed, ephemeral=True)
            else:
                await target.response.send_message(embed=embed, ephemeral=True)
        except discord.HTTPException:
            pass
        return

    if isinstance(target, commands.Context):
        try:
            await target.reply(embed=embed, mention_author=False)
        except discord.HTTPException:
            pass
