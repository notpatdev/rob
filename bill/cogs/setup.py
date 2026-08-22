from __future__ import annotations

from typing import TYPE_CHECKING, cast

import discord
from discord import app_commands
from discord.ext import commands

from bill.worker_client import WorkerAPIError

if TYPE_CHECKING:
    from bill.bot import BillBot


def missing_channel_permissions(permissions: discord.Permissions) -> tuple[str, ...]:
    required = {
        "view_channel": "View Channel",
        "send_messages": "Send Messages",
        "embed_links": "Embed Links",
        "read_message_history": "Read Message History",
    }
    return tuple(label for name, label in required.items() if not getattr(permissions, name))


class BillSetupCog(commands.Cog):
    bill = app_commands.Group(name="bill", description="Configure Bill for this server")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = cast("BillBot", bot)

    @bill.command(name="setup", description="Choose where Bill posts Throne sends")
    @app_commands.describe(send_channel="The channel where Bill should post sends")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def setup(
        self,
        interaction: discord.Interaction,
        send_channel: discord.TextChannel,
    ) -> None:
        if (
            not isinstance(interaction.user, discord.Member)
            or not interaction.user.guild_permissions.manage_guild
        ):
            await interaction.response.send_message(
                "You need **Manage Server** to configure Bill.",
                ephemeral=True,
            )
            return
        me = interaction.guild.me if interaction.guild else None
        if me is None:
            await interaction.response.send_message(
                "Bill could not check its server permissions. Please try again.",
                ephemeral=True,
            )
            return
        missing = missing_channel_permissions(send_channel.permissions_for(me))
        if missing:
            missing_text = ", ".join(f"**{item}**" for item in missing)
            await interaction.response.send_message(
                f"Bill needs {missing_text} in {send_channel.mention}.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            worker = self.bot.require_worker()
            await worker.configure_guild(
                guild_id=interaction.guild_id,
                send_channel_id=send_channel.id,
            )
        except WorkerAPIError as exc:
            await interaction.followup.send(
                f"Bill could not save that channel: {exc}",
                ephemeral=True,
            )
            return
        await interaction.followup.send(
            f"Bill is ready to post Throne sends in {send_channel.mention}. "
            "Dom/mes can now run `/register domme`.",
            ephemeral=True,
        )
