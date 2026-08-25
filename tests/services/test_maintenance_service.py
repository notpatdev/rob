from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from rob.services.leaderboard_status import LeaderboardStatus
from rob.services.maintenance_service import (
    LEADERBOARD_REFRESH_REQUESTED_AT_KEY,
    MAINTENANCE_MODE_KEY,
    MAINTENANCE_REASON_KEY,
    MaintenanceService,
)


class _FakeBotState:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.updated = datetime.now(timezone.utc)

    async def get_values(self, keys: list[str]) -> dict[str, str]:
        return {key: self.values[key] for key in keys if key in self.values}

    async def get_value(self, key: str):
        return self.values.get(key), self.updated

    async def get_bool(self, key: str, *, default: bool = False) -> bool:
        value = self.values.get(key)
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "on"}

    async def set_values(self, values: dict[str, str | None]) -> None:
        for key, value in values.items():
            if value is None:
                self.values.pop(key, None)
            else:
                self.values[key] = value

    async def set_value(self, key: str, value: str) -> None:
        self.values[key] = value


def test_enable_sets_maintenance_and_requests_leaderboard_refresh():
    state = _FakeBotState()
    service = MaintenanceService(state)

    asyncio.run(service.enable(reason="Deploying"))

    assert state.values[MAINTENANCE_MODE_KEY] == "true"
    assert state.values[MAINTENANCE_REASON_KEY] == "Deploying"
    assert LEADERBOARD_REFRESH_REQUESTED_AT_KEY in state.values


def test_disable_clears_maintenance_and_requests_leaderboard_refresh():
    state = _FakeBotState()
    service = MaintenanceService(state)

    asyncio.run(service.disable())

    assert state.values[MAINTENANCE_MODE_KEY] == "false"
    assert state.values[MAINTENANCE_REASON_KEY] == ""
    assert LEADERBOARD_REFRESH_REQUESTED_AT_KEY in state.values


def test_leaderboard_status_returns_maintenance_or_live():
    state = _FakeBotState()
    service = MaintenanceService(state)

    assert asyncio.run(service.get_leaderboard_status()) == LeaderboardStatus.LIVE

    asyncio.run(service.enable(reason=None))
    assert asyncio.run(service.get_leaderboard_status()) == LeaderboardStatus.MAINTENANCE
    assert asyncio.run(service.registrations_blocked()) is True
    assert asyncio.run(service.notifications_suppressed()) is True


def test_get_state_parses_legacy_json_wrapped_values():
    state = _FakeBotState()
    state.values[MAINTENANCE_MODE_KEY] = '{"value": "true"}'
    state.values[MAINTENANCE_REASON_KEY] = '{"value": "Deploying update"}'
    service = MaintenanceService(state)

    result = asyncio.run(service.get_state())

    assert result.enabled is True
    assert result.reason == "Deploying update"
