"""``/shutdown`` — send Rob's farewell announcement to Rob users.

Owner-only. The command is hidden from regular members (``default_permissions``
requires Manage Server) and additionally gated in code to the configured
bot-owner user ids, since ``default_permissions`` is only a UI hint.

Recipients are deliberately limited to :data:`ANNOUNCEMENT_RECIPIENT_USER_IDS`.
For now that is the bot owner ONLY, so the announcement can be tested
end-to-end without reaching real users. Widen that tuple (e.g. to every
registered Dom/me + Sub, or every ``bot_users`` row) to broadcast for real.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from rob.config.guilds import (
    BOT_OWNER_USER_IDS,
    MAIN_GUILD_ID,
    OWNER_USER_ID,
    TEST_GUILD_ID,
)
from rob.ui.cards.errors import error_card
from rob.ui.cards.shutdown import shutdown_announcement_card, shutdown_sent_card

if TYPE_CHECKING:
    from rob.discord.client import RobBot

log = logging.getLogger(__name__)

# Who the announcement is delivered to. Testing-only for now: just the owner.
# To go live, widen this to the full Rob user base.
ANNOUNCEMENT_RECIPIENT_USER_IDS: tuple[int, ...] = (OWNER_USER_ID,)


class ShutdownCog(commands.Cog):
    def __init__(self, bot: "RobBot") -> None:
        self.bot = bot

    def _is_owner(self, user: discord.abc.User | None) -> bool:
        return user is not None and user.id in BOT_OWNER_USER_IDS

    async def _resolve_recipients(self) -> list[discord.abc.Messageable]:
        """Resolve the recipient ids to messageable users, de-duplicated."""
        recipients: list[discord.abc.Messageable] = []
        seen: set[int] = set()
        for user_id in ANNOUNCEMENT_RECIPIENT_USER_IDS:
            if user_id in seen:
                continue
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
            seen.add(user_id)
            recipients.append(user)
        return recipients

    async def _deliver(
        self, recipients: list[discord.abc.Messageable]
    ) -> tuple[int, int]:
        """DM the announcement to each recipient; return (sent, failed)."""
        sent = 0
        failed = 0
        for recipient in recipients:
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
        description="Send Rob's shutdown announcement (owner only).",
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
        recipients = await self._resolve_recipients()
        sent, failed = await self._deliver(recipients)
        log.info(
            "Shutdown announcement sent by user_id=%s sent=%s failed=%s",
            getattr(interaction.user, "id", None),
            sent,
            failed,
        )
        await interaction.followup.send(
            **shutdown_sent_card(sent=sent, failed=failed).send_kwargs(),
            ephemeral=True,
        )
