"""``/shutdown`` — send Rob's farewell announcement to registered Rob users.

Owner-only to run: the command is hidden from regular members
(``default_permissions`` requires Manage Server) and additionally gated in code
to the configured bot-owner user ids, since ``default_permissions`` is only a
UI hint.

The announcement DMs every registered Dom/me and Sub in the main guild, minus
blacklisted users. Because that is an irreversible mass DM, ``/shutdown`` first
shows an ephemeral preview with the recipient count and a Confirm / Cancel
step — nothing is sent until the owner confirms.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from rob.config.guilds import BOT_OWNER_USER_IDS, MAIN_GUILD_ID, TEST_GUILD_ID
from rob.ui.cards.errors import error_card
from rob.ui.cards.shutdown import shutdown_announcement_card, shutdown_sent_card
from rob.ui.theme import COLOR_NEUTRAL, COLOR_WARNING

if TYPE_CHECKING:
    from rob.discord.client import RobBot

log = logging.getLogger(__name__)

# The announcement is VIB-specific, so the audience is scoped to the main guild.
ANNOUNCEMENT_GUILD_ID = MAIN_GUILD_ID

# Gentle pacing between DMs so a mass send doesn't hammer Discord's rate limits.
SEND_DELAY_SECONDS = 1.0


class _ConfirmSendView(discord.ui.LayoutView):
    """Ephemeral "send to all N?" confirmation shown before the mass DM."""

    def __init__(
        self, *, cog: "ShutdownCog", invoker_id: int, recipient_count: int
    ) -> None:
        super().__init__(timeout=300)
        self._cog = cog
        self._invoker_id = invoker_id

        container = discord.ui.Container(accent_color=COLOR_WARNING)
        container.add_item(discord.ui.TextDisplay("-# Shutdown announcement"))
        container.add_item(discord.ui.TextDisplay("## Send to all Rob users?"))
        if recipient_count:
            body = (
                f"This will DM the announcement to **{recipient_count}** registered "
                "Dom/me(s) and Sub(s) in VIB. This can't be undone."
            )
        else:
            body = "No registered Dom/mes or Subs were found to send to."
        container.add_item(discord.ui.TextDisplay(body))
        container.add_item(discord.ui.Separator())

        confirm = discord.ui.Button(
            style=discord.ButtonStyle.danger,
            label=f"Send to {recipient_count}",
            disabled=recipient_count == 0,
        )
        confirm.callback = self._on_confirm  # type: ignore[assignment]
        cancel = discord.ui.Button(style=discord.ButtonStyle.secondary, label="Cancel")
        cancel.callback = self._on_cancel  # type: ignore[assignment]
        container.add_item(discord.ui.ActionRow(confirm, cancel))
        self.add_item(container)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Only the owner who opened the confirmation can act on it.
        if interaction.user is not None and interaction.user.id != self._invoker_id:
            await interaction.response.send_message(
                "This confirmation isn't yours.", ephemeral=True
            )
            return False
        return True

    async def _on_confirm(self, interaction: discord.Interaction) -> None:
        await self._cog.perform_send(interaction)

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        await self._cog.cancel_send(interaction)


class ShutdownCog(commands.Cog):
    def __init__(self, bot: "RobBot") -> None:
        self.bot = bot
        self.send_delay_seconds = SEND_DELAY_SECONDS

    def _is_owner(self, user: discord.abc.User | None) -> bool:
        return user is not None and user.id in BOT_OWNER_USER_IDS

    async def _resolve_recipient_ids(self) -> list[int]:
        """Distinct discord ids of registered Dom/mes + Subs, minus blacklisted.

        De-duplicated across the two tables and preserving Dom/mes-first order.
        """
        dommes = await self.bot.dommes_repo.list_for_guild(ANNOUNCEMENT_GUILD_ID)
        subs = await self.bot.subs_repo.list_for_guild(ANNOUNCEMENT_GUILD_ID)

        ids: list[int] = []
        seen: set[int] = set()
        for entry in [*dommes, *subs]:
            user_id = int(entry.discord_user_id)
            if user_id in seen:
                continue
            seen.add(user_id)
            if await self.bot.blacklist_repo.contains(user_id):
                continue
            ids.append(user_id)
        return ids

    async def _resolve_recipients(
        self, user_ids: list[int]
    ) -> list[discord.abc.Messageable]:
        """Resolve ids to messageable users, skipping any that can't be found."""
        recipients: list[discord.abc.Messageable] = []
        for user_id in user_ids:
            user = self.bot.get_user(user_id)
            if user is None:
                try:
                    user = await self.bot.fetch_user(user_id)
                except discord.HTTPException:
                    log.warning(
                        "Shutdown announcement: could not resolve user_id=%s",
                        user_id,
                    )
                    continue
            recipients.append(user)
        return recipients

    async def _deliver(
        self, recipients: list[discord.abc.Messageable]
    ) -> tuple[int, int]:
        """DM the announcement to each recipient; return (sent, failed).

        A short delay between sends keeps a mass DM under Discord's rate limits.
        """
        sent = 0
        failed = 0
        for index, recipient in enumerate(recipients):
            if index and self.send_delay_seconds:
                await asyncio.sleep(self.send_delay_seconds)
            # Build a fresh card per recipient: a LayoutView is bound to the
            # message it is sent with and must not be reused across sends.
            card = shutdown_announcement_card()
            try:
                await recipient.send(**card.send_kwargs())
                sent += 1
            except discord.HTTPException:
                failed += 1
                log.warning(
                    "Shutdown announcement: failed to DM user_id=%s",
                    getattr(recipient, "id", None),
                    exc_info=True,
                )
        return sent, failed

    @app_commands.command(
        name="shutdown",
        description="Send Rob's shutdown announcement to all registered users (owner only).",
    )
    @app_commands.guilds(MAIN_GUILD_ID, TEST_GUILD_ID)
    @app_commands.default_permissions(manage_guild=True)
    async def shutdown_command(self, interaction: discord.Interaction) -> None:
        if not self._is_owner(interaction.user):
            await interaction.response.send_message(
                **error_card(
                    "Only Rob's owner can send the shutdown announcement."
                ).send_kwargs(),
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        recipient_ids = await self._resolve_recipient_ids()
        view = _ConfirmSendView(
            cog=self,
            invoker_id=interaction.user.id,
            recipient_count=len(recipient_ids),
        )
        await interaction.followup.send(view=view, ephemeral=True)

    async def perform_send(self, interaction: discord.Interaction) -> None:
        """Deliver the announcement after the owner confirms."""
        await interaction.response.defer()
        recipient_ids = await self._resolve_recipient_ids()
        recipients = await self._resolve_recipients(recipient_ids)
        sent, failed = await self._deliver(recipients)
        log.info(
            "Shutdown announcement sent by user_id=%s sent=%s failed=%s",
            getattr(interaction.user, "id", None),
            sent,
            failed,
        )
        await interaction.edit_original_response(
            view=shutdown_sent_card(sent=sent, failed=failed).view
        )

    async def cancel_send(self, interaction: discord.Interaction) -> None:
        container = discord.ui.Container(accent_color=COLOR_NEUTRAL)
        container.add_item(discord.ui.TextDisplay("-# Shutdown announcement"))
        container.add_item(discord.ui.TextDisplay("## Cancelled"))
        container.add_item(discord.ui.TextDisplay("No announcements were sent."))
        view: discord.ui.LayoutView = discord.ui.LayoutView(timeout=1)
        view.add_item(container)
        await interaction.response.edit_message(view=view)
