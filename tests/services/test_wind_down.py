from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from rob.services import wind_down
from rob.services.maintenance_service import MaintenanceService


def _utc(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def test_compute_phase_across_the_boundaries():
    # 8am AEST == 22:00 UTC the previous day (AEST = UTC+10).
    assert wind_down.compute_wind_down_phase(_utc(2026, 7, 10)) == 0
    assert wind_down.compute_wind_down_phase(_utc(2026, 7, 15, 21, 59)) == 0
    assert wind_down.compute_wind_down_phase(_utc(2026, 7, 15, 22, 0)) == 1  # 16 Jul 8am
    assert wind_down.compute_wind_down_phase(_utc(2026, 7, 18)) == 1
    assert wind_down.compute_wind_down_phase(_utc(2026, 7, 19, 22, 0)) == 2  # 20 Jul 8am
    assert wind_down.compute_wind_down_phase(_utc(2026, 7, 25)) == 2
    assert wind_down.compute_wind_down_phase(_utc(2026, 7, 31, 22, 0)) == 3  # 1 Aug 8am
    assert wind_down.compute_wind_down_phase(_utc(2026, 8, 5)) == 3


class _FakeBotState:
    def __init__(self):
        self.store: dict[str, str] = {}

    async def get_text(self, key):
        return self.store.get(key)

    async def set_value(self, key, value):
        self.store[key] = value

    async def get_bool(self, key, default=False):
        raw = self.store.get(key)
        if raw is None:
            return default
        return str(raw).strip().lower() in {"1", "true", "yes", "on"}

    async def set_bool(self, key, value):
        self.store[key] = "true" if value else "false"


def test_get_phase_defaults_to_zero_and_set_round_trips():
    state = _FakeBotState()
    assert asyncio.run(wind_down.get_phase(state)) == 0
    asyncio.run(wind_down.set_phase(state, 2))
    assert asyncio.run(wind_down.get_phase(state)) == 2


def test_set_phase_clamps_to_valid_range():
    state = _FakeBotState()
    assert asyncio.run(wind_down.set_phase(state, 9)) == wind_down.MAX_PHASE
    assert asyncio.run(wind_down.set_phase(state, -3)) == 0


def test_get_phase_tolerates_garbage():
    state = _FakeBotState()
    state.store[wind_down.WIND_DOWN_PHASE_KEY] = "not a number"
    assert asyncio.run(wind_down.get_phase(state)) == 0


def test_auto_advance_defaults_on_and_toggles():
    state = _FakeBotState()
    assert asyncio.run(wind_down.is_auto_advance_enabled(state)) is True
    asyncio.run(wind_down.set_auto_advance(state, False))
    assert asyncio.run(wind_down.is_auto_advance_enabled(state)) is False


def test_maintenance_service_phase_accessors_round_trip():
    service = MaintenanceService(_FakeBotState())
    assert asyncio.run(service.get_wind_down_phase()) == 0
    asyncio.run(service.set_wind_down_phase(3))
    assert asyncio.run(service.get_wind_down_phase()) == 3
    assert asyncio.run(service.wind_down_auto_advance()) is True
    asyncio.run(service.set_wind_down_auto_advance(False))
    assert asyncio.run(service.wind_down_auto_advance()) is False
