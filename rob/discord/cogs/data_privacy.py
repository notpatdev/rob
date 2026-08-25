"""Privacy / right-to-erasure: ``/forgetme`` + auto-wipe on main-guild leave.

Two entry points share one wipe path (:class:`UserDataRepository`):

* ``/forgetme`` — a member self-erases their Rob data. The command never
  deletes on the first click: it shows an ephemeral confirmation card with a
  "Yes, delete everything" / "Cancel" pair. When the user has data in more than
  one guild the card instead offers a scope choice — "Just this server" vs
  "Everywhere (all servers)" — alongside Cancel. After deleting, an ephemeral
  summary reports how many records were removed and the scope.

* ``on_member_remove`` (gated strictly to ``MAIN_GUILD_ID``) — when someone
  leaves the main guild, all of their data is erased everywhere. Leaving any
  other guild does nothing.

Neither path touches ``bot_users`` (block status) — a blocked user stays
blocked.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands

from rob.config.guilds import MAIN_GUILD_ID
from rob.ui.theme import COLOR_DANGER, COLOR_NEUTRAL, COLOR_SUCCESS

if TYPE_CHECKING:
    from rob.discord.client import RobBot
    from rob.database.repositories.user_data import UserDataRepository

log = logging.getLogger(__name__)


def _summarize(deleted: dict[str, int]) -> tuple[int, str]:
    """Return ``(total_rows, human_breakdown)`` for a wipe result mapping."""

    total = sum(deleted.values())
    touched = {table: count for table, count in deleted.items() if count}
    if not touched:
        return 0, "Nothing was stored, so there was nothing to remove."
    lines = "\n".join(
        f"- **{table}**: {count}" for table, count in sorted(touched.items())
    )
    return total, lines


class _ForgetMeView(discord.ui.LayoutView):
    """Ephemeral confirmation card for ``/forgetme``.

    ``multi_guild`` decides the layout: a simple confirm/cancel pair, or a
    scope choice (this server / everywhere) plus cancel. Button callbacks are
    bound to the owning cog so the flow stays unit-testable.
    """

    def __init__(
        self,
        *,
        cog: "DataPrivacyCog",
        user_id: int,
        guild_id: int | None,
        multi_guild: bool,
    ) -> None:
        super().__init__(timeout=180)
        self._cog = cog
        self._user_id = user_id
        self._guild_id = guild_id

        container = discord.ui.Container(accent_color=COLOR_DANGER)
        container.add_item(discord.ui.TextDisplay("-# Privacy"))
        container.add_item(discord.ui.TextDisplay("## Delete all of your data?"))
        container.add_item(
            discord.ui.TextDisplay(
                "This permanently removes your Rob records — registrations, "
                "logged sends, counting state and onboarding. It can't be undone."
            )
        )
        container.add_item(discord.ui.Separator())

        if multi_guild:
            container.add_item(
                discord.ui.TextDisplay(
                    "You have data in more than one server. Choose what to delete:"
                )
            )
            this_server = discord.ui.Button(
                style=discord.ButtonStyle.danger,
                label="Just this server",
            )
            this_server.callback = self._on_this_server  # type: ignore[assignment]
            everywhere = discord.ui.Button(
                style=discord.ButtonStyle.danger,
                label="Everywhere (all servers)",
            )
            everywhere.callback = self._on_everywhere  # type: ignore[assignment]
            cancel = discord.ui.Button(
                style=discord.ButtonStyle.secondary,
                label="Cancel",
            )
            cancel.callback = self._on_cancel  # type: ignore[assignment]
            container.add_item(
                discord.ui.ActionRow(this_server, everywhere, cancel)
            )
        else:
            confirm = discord.ui.Button(
                style=discord.ButtonStyle.danger,
                label="Yes, delete everything",
            )
            confirm.callback = self._on_confirm_single  # type: ignore[assignment]
            cancel = discord.ui.Button(
                style=discord.ButtonStyle.secondary,
                label="Cancel",
            )
            cancel.callback = self._on_cancel  # type: ignore[assignment]
            container.add_item(discord.ui.ActionRow(confirm, cancel))

        self.add_item(container)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Only the original requester can act on their own deletion card.
        if interaction.user is not None and interaction.user.id != self._user_id:
            await interaction.response.send_message(
                "This confirmation isn't yours.", ephemeral=True
            )
            return False
        return True

    async def _on_this_server(self, interaction: discord.Interaction) -> None:
        await self._cog.perform_deletion(
            interaction, scope="guild", guild_id=self._guild_id
        )

    async def _on_everywhere(self, interaction: discord.Interaction) -> None:
        await self._cog.perform_deletion(interaction, scope="everywhere")

    async def _on_confirm_single(self, interaction: discord.Interaction) -> None:
        # A single-guild requester has data in at most one place, so "delete
        # everything" wipes everywhere. This stays correct even when /forgetme
        # is invoked from a different server (or a DM) than the one holding the
        # data, and it also clears their guild-less terms record.
        await self._cog.perform_deletion(interaction, scope="everywhere")

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        card = discord.ui.Container(accent_color=COLOR_NEUTRAL)
        card.add_item(discord.ui.TextDisplay("-# Privacy"))
        card.add_item(discord.ui.TextDisplay("## Cancelled"))
        card.add_item(
            discord.ui.TextDisplay("No data was deleted. Nothing has changed.")
        )
        view: discord.ui.LayoutView = discord.ui.LayoutView(timeout=1)
        view.add_item(card)
        await interaction.response.edit_message(view=view)


class DataPrivacyCog(commands.Cog):
    def __init__(self, bot: "RobBot") -> None:
        self.bot = bot

    @property
    def _user_data_repo(self) -> "UserDataRepository":
        return self.bot.user_data_repo

    async def perform_deletion(
        self,
        interaction: discord.Interaction,
        *,
        scope: str,
        guild_id: int | None = None,
    ) -> None:
        """Run the wipe for the chosen scope and edit the card to a summary."""

        user_id = interaction.user.id
        try:
            if scope == "guild":
                if guild_id is None:  # pragma: no cover - guarded by callers
                    await interaction.response.send_message(
                        "Couldn't tell which server to delete from.",
                        ephemeral=True,
                    )
                    return
                deleted = await self._user_data_repo.delete_user_in_guild(
                    user_id, guild_id
                )
                scope_label = "this server"
            else:
                deleted = await self._user_data_repo.delete_user_everywhere(user_id)
                scope_label = "all servers"
        except Exception:
            log.exception(
                "Failed to erase data for user_id=%s scope=%s guild_id=%s",
                user_id,
                scope,
                guild_id,
            )
            await interaction.response.edit_message(
                view=self._result_view(
                    title="Something went wrong",
                    body=(
                        "Rob couldn't finish deleting your data. Nothing was "
                        "partially removed — please try again later."
                    ),
                    color=COLOR_DANGER,
                )
            )
            return

        total, breakdown = _summarize(deleted)
        log.info(
            "Erased data for user_id=%s scope=%s guild_id=%s total_rows=%s breakdown=%s",
            user_id,
            scope,
            guild_id,
            total,
            deleted,
        )
        await interaction.response.edit_message(
            view=self._result_view(
                title="Your data has been deleted",
                body=(
                    f"Removed **{total}** record(s) from **{scope_label}**.\n\n"
                    f"{breakdown}"
                ),
                color=COLOR_SUCCESS,
            )
        )

    @staticmethod
    def _result_view(
        *, title: str, body: str, color: discord.Colour
    ) -> discord.ui.LayoutView:
        container = discord.ui.Container(accent_color=color)
        container.add_item(discord.ui.TextDisplay("-# Privacy"))
        container.add_item(discord.ui.TextDisplay(f"## {title}"))
        container.add_item(discord.ui.TextDisplay(body))
        view: discord.ui.LayoutView = discord.ui.LayoutView(timeout=1)
        view.add_item(container)
        return view

    @app_commands.command(
        name="forgetme",
        description="Delete all of your data from Rob.",
    )
    async def forgetme_command(self, interaction: discord.Interaction) -> None:
        user_id = interaction.user.id
        guild_id = interaction.guild_id
        try:
            guilds = await self._user_data_repo.guilds_with_user_data(user_id)
        except Exception:  # pragma: no cover - defensive
            log.exception("Failed to look up data scope for user_id=%s", user_id)
            guilds = []

        multi_guild = len(guilds) > 1
        view = _ForgetMeView(
            cog=self,
            user_id=user_id,
            guild_id=guild_id,
            multi_guild=multi_guild,
        )
        await interaction.response.send_message(view=view, ephemeral=True)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        """Erase everything for a user who LEAVES THE MAIN GUILD.

        Gated strictly to ``MAIN_GUILD_ID`` — leaving any other guild does
        nothing. The wipe spans every guild (``delete_user_everywhere``).
        """

        guild: Any = getattr(member, "guild", None)
        if guild is None or guild.id != MAIN_GUILD_ID:
            return
        try:
            deleted = await self._user_data_repo.delete_user_everywhere(member.id)
        except Exception:
            log.exception(
                "Failed to auto-erase data for user_id=%s leaving main guild",
                member.id,
            )
            return
        total = sum(deleted.values())
        log.info(
            "Auto-erased data for user_id=%s on main-guild leave total_rows=%s breakdown=%s",
            member.id,
            total,
            deleted,
        )
