"""Rob's 1 August final sequence — the last thing Rob ever does.

Fires once when the wind-down reaches its final phase (3). Three steps, in a
strict order because the last one is irreversible:

1. **Final stats** — post Rob's closing numbers for VIB to the leaderboard
   channel (best-effort; never blocks the rest).
2. **Farewell DMs** — DM every registered Dom/me and Sub a thank-you message
   with their personal keepsake PDF attached. Must run *before* anonymisation,
   since the PDFs are built from each person's own send history.
3. **Anonymise** — irreversibly strip identities from every send, keeping the
   amounts/currencies/dates that still power VIB's public totals.

The whole thing is idempotent and resumable: each step sets its own flag in the
shared ``bot_settings`` table, so a restart part-way through skips finished steps
and never repeats the mass DM or the anonymisation. A process-local lock stops
two overlapping triggers (the wind-down loop and a manual force) from running it
twice at once.
"""

from __future__ import annotations

import asyncio
import io
import logging
from typing import TYPE_CHECKING

import discord

from rob.config.guilds import MAIN_GUILD_ID
from rob.database.repositories.public_summary import PublicSummaryRepository
from rob.reports.sends_pdf import build_recipient_report, generate_sends_pdf
from rob.services.recipients import (
    resolve_messageable_recipients,
    resolve_registered_recipient_ids,
)
from rob.services.wind_down import MAX_PHASE
from rob.ui.cards.final_sequence import (
    KEEPSAKE_PDF_FILENAME,
    final_stats_card,
    final_thank_you_card,
)
from rob.utils.time import utc_now

if TYPE_CHECKING:
    from rob.discord.client import RobBot

log = logging.getLogger(__name__)

# The wind-down phase at which the final sequence fires.
FINAL_PHASE = MAX_PHASE

# Idempotency flags in bot_settings. Each step sets its own when done.
STATS_POSTED_KEY = "final_sequence_stats_posted"
DMS_SENT_KEY = "final_sequence_dms_sent"
ANONYMISED_KEY = "final_sequence_anonymised"
COMPLETED_KEY = "final_sequence_completed"

# Gentle pacing between farewell DMs so a mass send stays under the rate limit.
SEND_DELAY_SECONDS = 1.0


class FinalSequenceService:
    def __init__(
        self,
        *,
        bot: "RobBot",
        guild_id: int = MAIN_GUILD_ID,
        send_delay_seconds: float = SEND_DELAY_SECONDS,
    ) -> None:
        self.bot = bot
        self.guild_id = guild_id
        self.send_delay_seconds = send_delay_seconds
        self._lock = asyncio.Lock()

    @property
    def _settings(self):
        return self.bot.bot_settings_repo

    async def _flag(self, key: str) -> bool:
        return await self._settings.get_bool(key, default=False)

    async def _set_flag(self, key: str) -> None:
        await self._settings.set_bool(key, True)

    async def maybe_run(self) -> bool:
        """Run the final sequence when the wind-down has reached its final phase.

        Idempotent: returns ``False`` and does nothing before the final phase or
        once the sequence has completed. Safe to call repeatedly (e.g. from the
        wind-down loop) — a process-local lock prevents overlap.
        """
        phase = await self.bot.maintenance_service.get_wind_down_phase()
        if phase < FINAL_PHASE:
            return False
        if await self._flag(COMPLETED_KEY):
            return False
        if self._lock.locked():
            return False
        async with self._lock:
            # Re-check under the lock in case a concurrent run just finished.
            if await self._flag(COMPLETED_KEY):
                return False
            return await self._run_steps()

    async def _run_steps(self) -> bool:
        log.warning("Final sequence: starting (wind-down phase %s).", FINAL_PHASE)

        # 1. Final stats — best-effort, and posted before identities are wiped.
        # A failure here (e.g. the channel is momentarily unavailable) must never
        # block the irreversible steps, so we flag it done after one attempt.
        if not await self._flag(STATS_POSTED_KEY):
            try:
                await self._post_final_stats()
            except Exception:
                log.exception("Final sequence: posting final stats failed; continuing.")
            await self._set_flag(STATS_POSTED_KEY)

        # 2. Farewell DMs + keepsake PDFs — must precede anonymisation.
        if not await self._flag(DMS_SENT_KEY):
            sent, failed = await self._send_farewell_dms()
            log.warning("Final sequence: farewell DMs sent=%s failed=%s.", sent, failed)
            await self._set_flag(DMS_SENT_KEY)

        # 3. Anonymise send data — irreversible, only after the DMs are out.
        if not await self._flag(ANONYMISED_KEY):
            updated = await self.bot.sends_repo.anonymise_guild_sends(self.guild_id)
            log.warning("Final sequence: anonymised %s send(s).", updated)
            await self._set_flag(ANONYMISED_KEY)

        await self._set_flag(COMPLETED_KEY)
        log.warning("Final sequence: complete.")
        return True

    async def _post_final_stats(self) -> None:
        summary = await PublicSummaryRepository(self.bot.database).guild_summary(
            guild_id=self.guild_id
        )
        channel = await self._leaderboard_channel()
        if channel is None:
            log.error("Final sequence: leaderboard channel unavailable; no stats post.")
            return
        await channel.send(**final_stats_card(summary).send_kwargs())

    async def _leaderboard_channel(self) -> discord.TextChannel | None:
        settings = await self.bot.vib_settings_repo.get(self.guild_id)
        if settings is None or settings.leaderboard_channel_id is None:
            return None
        guild = self.bot.get_guild(self.guild_id)
        if guild is None:
            return None
        channel = guild.get_channel(settings.leaderboard_channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(settings.leaderboard_channel_id)
            except (discord.NotFound, discord.HTTPException):
                return None
        return channel if isinstance(channel, discord.TextChannel) else None

    async def _send_farewell_dms(self) -> tuple[int, int]:
        """DM each registered Dom/me + Sub their thank-you and keepsake PDF.

        Returns ``(sent, failed)``. Individual failures (closed DMs, blocks) are
        logged and counted, never raised — one bad recipient can't stop the rest.
        """
        recipient_ids = await resolve_registered_recipient_ids(self.bot, self.guild_id)
        recipients = await resolve_messageable_recipients(self.bot, recipient_ids)
        generated_at = utc_now()

        sent = 0
        failed = 0
        for index, recipient in enumerate(recipients):
            if index and self.send_delay_seconds:
                await asyncio.sleep(self.send_delay_seconds)
            try:
                await self._send_one_farewell(recipient, generated_at=generated_at)
                sent += 1
            except discord.HTTPException:
                failed += 1
                log.warning(
                    "Final sequence: failed to DM user_id=%s",
                    getattr(recipient, "id", None),
                    exc_info=True,
                )
        return sent, failed

    async def _send_one_farewell(
        self, recipient: discord.abc.Messageable, *, generated_at
    ) -> None:
        user_id = int(getattr(recipient, "id", 0))
        rows = await self.bot.sends_repo.counted_sends_for_recipient(
            self.guild_id, user_id
        )
        display_name = (
            getattr(recipient, "display_name", None)
            or getattr(recipient, "name", None)
            or "friend"
        )
        report = build_recipient_report(
            display_name=display_name,
            generated_at=generated_at,
            rows=rows,
        )
        pdf = generate_sends_pdf(report)
        # Send the farewell card, then the keepsake PDF as its own follow-up.
        # Components V2 views suppress attachment previews, so the file ships as
        # a separate message (matching /report's delivery). A fresh LayoutView
        # per send: a view is bound to the message it ships with.
        await recipient.send(**final_thank_you_card().send_kwargs())
        attachment = discord.File(io.BytesIO(pdf), filename=KEEPSAKE_PDF_FILENAME)
        await recipient.send(file=attachment)
