from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from rob.config.guilds import OWNER_USER_ID
from rob.discord.cogs.wind_down import WindDownCog
from rob.services.maintenance_service import MaintenanceService
from tests.services.test_wind_down import _FakeBotState


def _utc(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


class _FakeBot:
    def __init__(self, maintenance):
        self.maintenance_service = maintenance

    async def wait_until_ready(self):  # pragma: no cover - loop is cancelled in tests
        return None


class _FakeResponse:
    def __init__(self):
        self.messages: list[dict] = []

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)


class _FakeInteraction:
    def __init__(self, user_id: int):
        self.user = SimpleNamespace(id=user_id)
        self.response = _FakeResponse()


def _make_cog():
    maintenance = MaintenanceService(_FakeBotState())
    cog = WindDownCog(_FakeBot(maintenance))
    cog.wind_down_loop.cancel()  # don't let the background loop tick during tests
    return cog, maintenance


def test_apply_due_phase_advances_to_the_due_phase():
    async def run():
        cog, maintenance = _make_cog()
        # Before any boundary: no change.
        assert await cog.apply_due_phase(now=_utc(2026, 7, 10)) is None
        assert await maintenance.get_wind_down_phase() == 0
        # At the 16 Jul boundary: advance to 1.
        assert await cog.apply_due_phase(now=_utc(2026, 7, 15, 22, 0)) == 1
        assert await maintenance.get_wind_down_phase() == 1
        # At the 20 Jul boundary: advance to 2.
        assert await cog.apply_due_phase(now=_utc(2026, 7, 19, 22, 0)) == 2
        assert await maintenance.get_wind_down_phase() == 2

    asyncio.run(run())


def test_apply_due_phase_is_monotonic():
    async def run():
        cog, maintenance = _make_cog()
        await maintenance.set_wind_down_phase(2)
        # An earlier "now" must never regress the phase.
        assert await cog.apply_due_phase(now=_utc(2026, 7, 16)) is None
        assert await maintenance.get_wind_down_phase() == 2

    asyncio.run(run())


def test_apply_due_phase_respects_auto_pause():
    async def run():
        cog, maintenance = _make_cog()
        await maintenance.set_wind_down_auto_advance(False)
        # Even well past the final boundary, paused auto-advance changes nothing.
        assert await cog.apply_due_phase(now=_utc(2026, 8, 10)) is None
        assert await maintenance.get_wind_down_phase() == 0

    asyncio.run(run())


def test_winddown_command_rejects_non_owner():
    async def run():
        cog, maintenance = _make_cog()
        interaction = _FakeInteraction(user_id=42)
        await WindDownCog.winddown_command.callback(cog, interaction, phase=3, auto=False)
        # Nothing changed; an ephemeral error was returned.
        assert await maintenance.get_wind_down_phase() == 0
        assert interaction.response.messages[0]["ephemeral"] is True

    asyncio.run(run())


def test_winddown_command_owner_can_force_phase_and_toggle_auto():
    async def run():
        cog, maintenance = _make_cog()
        interaction = _FakeInteraction(user_id=OWNER_USER_ID)
        await WindDownCog.winddown_command.callback(cog, interaction, phase=2, auto=False)
        assert await maintenance.get_wind_down_phase() == 2
        assert await maintenance.wind_down_auto_advance() is False
        assert interaction.response.messages[0]["ephemeral"] is True

    asyncio.run(run())
