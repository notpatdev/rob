"""Scheduled wind-down: a background loop that advances Rob's ``wind_down_phase``
as each dated boundary passes, plus an owner ``/winddown`` control.

The loop is monotonic (only advances) and idempotent — every tick re-derives the
due phase from the clock, so a restart or a missed tick self-corrects. Features
read the phase flag and behave accordingly; because the flag lives in the shared
``bot_settings`` table, the separate webhook process picks up changes too.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

from rob.config.guilds import BOT_OWNER_USER_IDS, MAIN_GUILD_ID, TEST_GUILD_ID
from rob.services.final_sequence import FINAL_PHASE
from rob.services.wind_down import MAX_PHASE, compute_wind_down_phase
from rob.ui.cards.errors import error_card
from rob.ui.components import make_card, render
from rob.ui.theme import COLOR_INFO, COLOR_WARNING
from rob.utils.time import utc_now

if TYPE_CHECKING:
    from rob.discord.client import RobBot

log = logging.getLogger(__name__)

_PHASE_SUMMARY = {
    0: "Normal operation.",
    1: "Sends no longer posted/leaderboarded; inactivity, warn relay and new registrations off. Recording, /add and the count still run.",
    2: "Webhook recording off and /add closed (URLs invalidated). Only the count runs.",
    3: "Rob is offline — the count is off too.",
}


class WindDownCog(commands.Cog):
    def __init__(self, bot: "RobBot") -> None:
        self.bot = bot
        self.wind_down_loop.start()

    def cog_unload(self) -> None:
        self.wind_down_loop.cancel()

    def _is_owner(self, user: discord.abc.User | None) -> bool:
        return user is not None and user.id in BOT_OWNER_USER_IDS

    async def apply_due_phase(self, *, now: datetime | None = None) -> int | None:
        """Advance the stored phase to the one due at ``now`` if auto-advance is
        on and it's higher than the current phase. Returns the new phase or None."""
        maintenance = self.bot.maintenance_service
        if not await maintenance.wind_down_auto_advance():
            return None
        current = await maintenance.get_wind_down_phase()
        target = compute_wind_down_phase(now or utc_now())
        if target > current:
            await maintenance.set_wind_down_phase(target)
            log.warning("Wind-down auto-advanced from phase %s to %s.", current, target)
            return target
        return None

    def _final_sequence_service(self):
        return getattr(self.bot, "final_sequence_service", None)

    async def _run_final_sequence(self) -> None:
        """Fire the 1 August final sequence if we've reached the final phase.

        Self-guards on phase and completion, so calling it every tick is safe.
        """
        service = self._final_sequence_service()
        if service is None:
            return
        try:
            await service.maybe_run()
        except Exception:  # pragma: no cover - safety around the runtime loop
            log.exception("Final sequence run failed.")

    @tasks.loop(minutes=15)
    async def wind_down_loop(self) -> None:
        try:
            await self.apply_due_phase()
            await self._run_final_sequence()
        except Exception:  # pragma: no cover - safety around the runtime loop
            log.exception("Wind-down loop failed.")

    @wind_down_loop.before_loop
    async def _before_wind_down_loop(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(
        name="winddown",
        description="View or control Rob's wind-down phase (owner only).",
    )
    @app_commands.guilds(MAIN_GUILD_ID, TEST_GUILD_ID)
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.describe(
        phase="Force the wind-down phase (0-3).",
        auto="Turn automatic date-driven advancement on or off.",
    )
    async def winddown_command(
        self,
        interaction: discord.Interaction,
        phase: app_commands.Range[int, 0, MAX_PHASE] | None = None,
        auto: bool | None = None,
    ) -> None:
        if not self._is_owner(interaction.user):
            await interaction.response.send_message(
                **error_card("Only Rob's owner can control the wind-down.").send_kwargs(),
                ephemeral=True,
            )
            return

        maintenance = self.bot.maintenance_service
        if auto is not None:
            await maintenance.set_wind_down_auto_advance(auto)
        if phase is not None:
            await maintenance.set_wind_down_phase(int(phase))
            log.warning(
                "Wind-down phase set to %s manually by user_id=%s.",
                int(phase),
                getattr(interaction.user, "id", None),
            )
            # Kick the final sequence without blocking the command's reply — the
            # mass DM can take a while. If it's not the final phase, maybe_run()
            # is a no-op. The loop would pick it up within 15 min regardless.
            if int(phase) >= FINAL_PHASE and self._final_sequence_service() is not None:
                asyncio.create_task(self._run_final_sequence())

        current = await maintenance.get_wind_down_phase()
        auto_on = await maintenance.wind_down_auto_advance()
        card = make_card(
            title=f"Wind-down — Phase {current}",
            body=_PHASE_SUMMARY.get(current, "Unknown phase."),
            color=COLOR_WARNING if current else COLOR_INFO,
            eyebrow="Wind-down",
            footer=f"Auto-advance: {'on' if auto_on else 'off'}",
        )
        await interaction.response.send_message(
            **render(card).send_kwargs(), ephemeral=True
        )
