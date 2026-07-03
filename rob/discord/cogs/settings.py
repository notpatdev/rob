"""/preferences: lets a member toggle their leaderboard access role."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from rob.config.guilds import MAIN_GUILD_ID, TEST_GUILD_ID, is_new_system_guild
from rob.discord.leaderboard_access import apply_leaderboard_access
from rob.ui.cards.dm_onboarding import PreferencesView
from rob.ui.cards.errors import error_card
from rob.ui.components import make_card, render
from rob.ui.theme import COLOR_SUCCESS

if TYPE_CHECKING:
    from rob.discord.client import RobBot


log = logging.getLogger(__name__)


def _not_available_response() -> dict:
    return error_card(
        "Not available here",
        "`/preferences` is only available in the test guild right now.",
    ).send_kwargs()


class SettingsCog(commands.Cog):
    def __init__(self, bot: "RobBot") -> None:
        self.bot = bot

    async def _send_preferences_panel(self, interaction: discord.Interaction) -> None:
        """Send the leaderboard-access panel; on save Rob syncs the role."""
        if not is_new_system_guild(interaction.guild_id):
            await interaction.response.send_message(**_not_available_response(), ephemeral=True)
            return

        member = interaction.user
        settings = await self.bot.guild_settings_repo.get(interaction.guild_id)
        access_role_id = getattr(settings, "leaderboard_view_role_id", None) if settings else None
        if access_role_id is None:
            await interaction.response.send_message(
                **error_card(
                    "Nothing to configure yet",
                    "There aren't any Rob preferences available to you here right now. "
                    "Ask staff to set up the leaderboard access role.",
                ).send_kwargs(),
                ephemeral=True,
            )
            return

        has_access = isinstance(member, discord.Member) and any(
            role.id == access_role_id for role in member.roles
        )

        view = PreferencesView(
            default_leaderboard_access=has_access,
            intro_lines=(
                "## Your Rob preferences",
                "Pick your option below, then hit **Save preferences**.",
            ),
        )
        save_button = view.save_button

        async def _save_callback(inner: discord.Interaction) -> None:  # noqa: ANN001
            try:
                await apply_leaderboard_access(
                    self.bot,
                    guild_id=inner.guild_id,
                    user_id=inner.user.id,
                    enabled=view.chosen_leaderboard_access,
                )
            except Exception:  # pragma: no cover - defensive
                log.exception("Failed to save preferences for user_id=%s", inner.user.id)
                await inner.response.send_message(
                    **error_card("Couldn't save", "Please try again later.").send_kwargs(),
                    ephemeral=True,
                )
                return
            await inner.response.edit_message(
                **render(
                    make_card(
                        title="Preferences saved!",
                        body="Your Rob preferences have been updated.",
                        color=COLOR_SUCCESS,
                        variant="success",
                    )
                ).edit_kwargs()
            )

        save_button.callback = _save_callback  # type: ignore[assignment]

        await interaction.response.send_message(view=view, ephemeral=True)

    @app_commands.command(
        name="preferences",
        description="Change your leaderboard access.",
    )
    @app_commands.guilds(MAIN_GUILD_ID, TEST_GUILD_ID)
    async def preferences_command(self, interaction: discord.Interaction) -> None:
        await self._send_preferences_panel(interaction)
