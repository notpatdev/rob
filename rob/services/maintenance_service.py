from __future__ import annotations

import json

from rob.database.repositories.bot_state import BotStateRepository
from rob.config.guilds import is_main_guild
from rob.models import MaintenanceState
from rob.services import wind_down
from rob.services.leaderboard_status import LeaderboardStatus
from rob.utils.time import utc_now


MAINTENANCE_MODE_KEY = "maintenance_mode"
MAINTENANCE_REASON_KEY = "maintenance_reason"
LEADERBOARD_REFRESH_REQUESTED_AT_KEY = "leaderboard_refresh_requested_at"
LEADERBOARD_REFRESH_COMPLETED_AT_KEY = "leaderboard_refresh_completed_at"
ROB_OFFLINE_MODE_KEY = "rob_offline_mode"


def _normalize_setting_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    if not cleaned:
        return None
    if cleaned.startswith("{") and cleaned.endswith("}"):
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError:
            return cleaned
        if isinstance(parsed, dict):
            nested = parsed.get("value")
            if nested is None:
                return None
            nested_cleaned = str(nested).strip()
            return nested_cleaned or None
    return cleaned


class MaintenanceService:
    def __init__(self, bot_state: BotStateRepository) -> None:
        self.bot_state = bot_state

    async def get_state(self) -> MaintenanceState:
        values = await self.bot_state.get_values(
            [MAINTENANCE_MODE_KEY, MAINTENANCE_REASON_KEY]
        )
        raw_enabled = _normalize_setting_text(values.get(MAINTENANCE_MODE_KEY)) or "false"
        enabled = raw_enabled.strip().lower() in {"1", "true", "yes", "on"}
        reason = _normalize_setting_text(values.get(MAINTENANCE_REASON_KEY))
        _raw_value, updated_at = await self.bot_state.get_value(MAINTENANCE_MODE_KEY)
        return MaintenanceState(enabled=enabled, reason=reason, updated_at=updated_at)

    async def is_enabled(self) -> bool:
        return await self.bot_state.get_bool(MAINTENANCE_MODE_KEY, default=False)

    async def is_rob_offline_enabled(self) -> bool:
        return await self.bot_state.get_bool(ROB_OFFLINE_MODE_KEY, default=False)

    async def is_rob_offline_for_guild(self, guild_id: int | None) -> bool:
        return is_main_guild(guild_id) and await self.is_rob_offline_enabled()

    async def get_leaderboard_status(self, guild_id: int | None = None) -> LeaderboardStatus:
        if await self.is_rob_offline_for_guild(guild_id):
            return LeaderboardStatus.OFFLINE
        if await self.is_enabled():
            return LeaderboardStatus.MAINTENANCE
        return LeaderboardStatus.LIVE

    async def registrations_blocked(self) -> bool:
        return await self.is_enabled()

    async def registrations_blocked_for_guild(self, guild_id: int | None) -> bool:
        return await self.is_enabled() or await self.is_rob_offline_for_guild(guild_id)

    async def notifications_suppressed(self) -> bool:
        return await self.is_enabled()

    async def send_tracking_disabled_for_guild(self, guild_id: int | None) -> bool:
        return await self.is_rob_offline_for_guild(guild_id)

    async def count_recovery_disabled_for_guild(self, guild_id: int | None) -> bool:
        return await self.is_rob_offline_for_guild(guild_id)

    async def enable(self, *, reason: str | None) -> None:
        await self.bot_state.set_values(
            {
                MAINTENANCE_MODE_KEY: "true",
                MAINTENANCE_REASON_KEY: reason or "",
            }
        )
        await self.request_leaderboard_refresh()

    async def disable(self) -> None:
        await self.bot_state.set_values(
            {
                MAINTENANCE_MODE_KEY: "false",
                MAINTENANCE_REASON_KEY: "",
            }
        )
        await self.request_leaderboard_refresh()

    async def enable_rob_offline(self) -> None:
        await self.bot_state.set_value(ROB_OFFLINE_MODE_KEY, "true")
        await self.request_leaderboard_refresh()

    async def disable_rob_offline(self) -> None:
        await self.bot_state.set_value(ROB_OFFLINE_MODE_KEY, "false")
        await self.request_leaderboard_refresh()

    # --- Scheduled wind-down -------------------------------------------------

    async def get_wind_down_phase(self) -> int:
        return await wind_down.get_phase(self.bot_state)

    async def set_wind_down_phase(self, phase: int) -> int:
        applied = await wind_down.set_phase(self.bot_state, phase)
        await self.request_leaderboard_refresh()
        return applied

    async def wind_down_auto_advance(self) -> bool:
        return await wind_down.is_auto_advance_enabled(self.bot_state)

    async def set_wind_down_auto_advance(self, enabled: bool) -> None:
        await wind_down.set_auto_advance(self.bot_state, enabled)

    async def request_leaderboard_refresh(self) -> None:
        marker = utc_now().isoformat()
        await self.bot_state.set_value(LEADERBOARD_REFRESH_REQUESTED_AT_KEY, marker)

    async def consume_leaderboard_refresh_request(self) -> bool:
        requested, _ = await self.bot_state.get_value(LEADERBOARD_REFRESH_REQUESTED_AT_KEY)
        completed, _ = await self.bot_state.get_value(LEADERBOARD_REFRESH_COMPLETED_AT_KEY)
        if not requested or requested == completed:
            return False
        await self.bot_state.set_value(LEADERBOARD_REFRESH_COMPLETED_AT_KEY, requested)
        return True
