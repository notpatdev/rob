"""Rob's scheduled wind-down: a single phase flag that both the bot and the
webhook process read from ``bot_settings``.

Phases (all boundaries are 08:00 AEST, a fixed UTC+10 offset — Brisbane has no
daylight saving, so we express the instants directly rather than depending on a
tz database):

* 0 — normal operation.
* 1 — 16 Jul: stop posting/leaderboarding sends, inactivity + warn off,
  registrations closed. Backend recording, manual ``/add`` and the count stay.
* 2 — 20 Jul: webhook stops recording Throne sends, ``/add`` closed
  (webhook URLs are functionally invalidated — reversibly — by the recording
  gate).
* 3 — 01 Aug: everything off, including the count. The final "goodbye"
  sequence (PDF, thank-you DM, final stats, anonymisation) is built separately.

The scheduler only ever advances (monotonic); features simply read the phase
and behave accordingly.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from rob.database.repositories.bot_state import BotStateRepository

# The phase (0-3) and whether the clock-driven loop may auto-advance.
WIND_DOWN_PHASE_KEY = "wind_down_phase"
WIND_DOWN_AUTO_KEY = "wind_down_auto_advance"

MAX_PHASE = 3

# AEST = UTC+10 year-round (Brisbane, no DST).
_AEST = timezone(timedelta(hours=10))

# (phase, first instant the phase is active), ascending. 8am AEST each date.
PHASE_BOUNDARIES: tuple[tuple[int, datetime], ...] = (
    (1, datetime(2026, 7, 16, 8, 0, tzinfo=_AEST)),
    (2, datetime(2026, 7, 20, 8, 0, tzinfo=_AEST)),
    (3, datetime(2026, 8, 1, 8, 0, tzinfo=_AEST)),
)


def compute_wind_down_phase(
    now: datetime,
    boundaries: tuple[tuple[int, datetime], ...] = PHASE_BOUNDARIES,
) -> int:
    """The phase due at ``now`` (a timezone-aware datetime)."""
    phase = 0
    for candidate, boundary in boundaries:
        if now >= boundary:
            phase = candidate
    return phase


async def get_phase(bot_state: BotStateRepository) -> int:
    raw = await bot_state.get_text(WIND_DOWN_PHASE_KEY)
    if raw is None:
        return 0
    try:
        value = int(str(raw).strip())
    except ValueError:
        return 0
    return max(0, min(MAX_PHASE, value))


async def set_phase(bot_state: BotStateRepository, phase: int) -> int:
    clamped = max(0, min(MAX_PHASE, int(phase)))
    await bot_state.set_value(WIND_DOWN_PHASE_KEY, str(clamped))
    return clamped


async def is_auto_advance_enabled(bot_state: BotStateRepository) -> bool:
    return await bot_state.get_bool(WIND_DOWN_AUTO_KEY, default=True)


async def set_auto_advance(bot_state: BotStateRepository, enabled: bool) -> None:
    await bot_state.set_bool(WIND_DOWN_AUTO_KEY, enabled)
