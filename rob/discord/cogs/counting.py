from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from rob.config.guilds import MAIN_GUILD_ID
from rob.ui.cards.counting import (
    count_blocked_sub_card,
    count_failed_card,
    counting_same_user_reminder_card,
    counting_updated_card,
)
from rob.ui.cards.errors import error_card
from rob.ui.emojis import ROBNO

if TYPE_CHECKING:
    from rob.discord.client import RobBot


class CountingCog(commands.Cog):
    count_group = app_commands.Group(
        name="count", description="Counting controls.", guild_ids=[MAIN_GUILD_ID]
    )

    def __init__(self, bot: RobBot) -> None:
        self.bot = bot

    @count_group.command(name="set", description="Set the current counting number.")
    @app_commands.default_permissions(manage_guild=True)
    async def count_set(
        self,
        interaction: discord.Interaction,
        number: app_commands.Range[int, 0],
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                **error_card("This command can only be used in a server.").send_kwargs(),
                ephemeral=True,
            )
            return

        await self.bot.counting_service.set_current_number(interaction.guild.id, int(number))
        await interaction.response.send_message(
            **counting_updated_card(int(number)).send_kwargs(),
            ephemeral=True,
        )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        result = await self.bot.counting_service.process_message(message)
        if result is None:
            return
        async def _apply_reactions(*reactions: str) -> None:
            for reaction in reactions:
                try:
                    await message.add_reaction(reaction)
                except discord.HTTPException:
                    continue
        if result.success:
            await _apply_reactions(*(result.reactions or ()))
            return

        if result.reason == "same_user":
            try:
                await message.delete()
            except discord.HTTPException:
                pass
            reminder = await message.channel.send(**counting_same_user_reminder_card().send_kwargs())
            try:
                await reminder.delete(delay=15)
            except discord.HTTPException:
                pass
            return

        if result.reason == "paused_for_rescue":
            try:
                await message.delete()
            except discord.HTTPException:
                pass
            return

        if result.reason == "blocked_sub":
            try:
                await message.delete()
            except discord.HTTPException:
                pass
            if result.blocked_until is not None:
                await message.channel.send(
                    **count_blocked_sub_card(
                        blocked_until_unix=int(result.blocked_until.timestamp())
                    ).send_kwargs()
                )
            return

        if result.reason in {"wrong_number_sub_recovery", "wrong_number_domme_recovery"}:
            await _apply_reactions(*(result.reactions or ()))
            return

        if result.reason == "wrong_number_reset":
            await message.channel.send(**count_failed_card().send_kwargs())
            await _apply_reactions(*(result.reactions or ()))
            return

        await _apply_reactions(ROBNO)
