"""Public progressive guild setup command for Bill send notifications."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import discord
from discord import app_commands
from discord.ext import commands

from bill.components.setup import missing_channel_permissions, setup_view
from bill.worker_client import WorkerAPIError

if TYPE_CHECKING:
    from bill.bot import BillBot


class BillSetupCog(commands.Cog):
    bill = app_commands.Group(name="bill", description="Configure Bill for this server")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = cast("BillBot", bot)

    @bill.command(name="setup", description="Choose where Bill posts Throne sends")
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.guild_only()
    async def setup(self, interaction: discord.Interaction[discord.Client]) -> None:
        if (
            not isinstance(interaction.user, discord.Member)
            or not interaction.user.guild_permissions.manage_guild
        ):
            await interaction.response.send_message(
                "You need **Manage Server** to configure Bill.", ephemeral=True
            )
            return
        try:
            started = await self.bot.require_worker().start_guild_setup(
                guild_id=interaction.guild_id, initiator_user_id=interaction.user.id
            )
        except WorkerAPIError as exc:
            await interaction.response.send_message(
                f"Bill could not start setup: {exc}", ephemeral=True
            )
            return
        if started.resume_required:
            await interaction.response.send_message(
                "A Bill setup session is already in progress. "
                "Use its existing public setup message.",
                ephemeral=True,
            )
            return
        # It is public for moderator visibility, while callback authorization is initiator-bound.
        await interaction.response.send_message(view=setup_view(started.session), ephemeral=False)


__all__ = ["BillSetupCog", "missing_channel_permissions"]
