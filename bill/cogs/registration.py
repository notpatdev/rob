from __future__ import annotations

from typing import TYPE_CHECKING, cast

import discord
from discord import app_commands
from discord.ext import commands

from bill.worker_client import DommeRegistration, WorkerAPIError

if TYPE_CHECKING:
    from bill.bot import BillBot


def registration_embed(result: DommeRegistration) -> discord.Embed:
    if result.webhook_url:
        description = (
            f"Bill linked **@{result.throne_handle}**.\n\n"
            "1. Open your Throne creator webhook settings.\n"
            "2. Add the URL below exactly as shown.\n"
            "3. Use Throne's test action to confirm the connection.\n\n"
            f"```text\n{result.webhook_url}\n```\n"
            "Keep this URL private—it contains the secret that authorizes your webhook."
        )
        title = "Add Bill to your Throne webhooks"
    else:
        description = (
            f"Bill linked **@{result.throne_handle}** to this server. "
            "Your existing Bill webhook is still active, so there is nothing to change on Throne."
        )
        title = "Throne tracking is linked"
    return discord.Embed(
        title=title,
        description=description,
        color=discord.Color.from_rgb(99, 72, 214),
    )


class RegistrationCog(commands.Cog):
    register = app_commands.Group(name="register", description="Register for Bill send tracking")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = cast("BillBot", bot)

    @register.command(name="domme", description="Connect your Throne creator account")
    @app_commands.describe(
        throne="Your Throne username or full profile URL",
        reset_webhook="Replace your existing Bill webhook URL",
    )
    @app_commands.guild_only()
    async def domme(
        self,
        interaction: discord.Interaction,
        throne: str,
        reset_webhook: bool = False,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            worker = self.bot.require_worker()
            config = await worker.get_guild_config(interaction.guild_id)
            if config is None:
                await interaction.followup.send(
                    "An administrator needs to run `/bill setup` before anyone can register.",
                    ephemeral=True,
                )
                return
            result = await worker.register_domme(
                guild_id=interaction.guild_id,
                discord_user_id=interaction.user.id,
                throne=throne,
                reset_webhook=reset_webhook,
            )
        except WorkerAPIError as exc:
            await interaction.followup.send(
                f"Bill could not connect that Throne account: {exc}",
                ephemeral=True,
            )
            return
        await interaction.followup.send(embed=registration_embed(result), ephemeral=True)
