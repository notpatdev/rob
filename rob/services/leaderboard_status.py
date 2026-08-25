from __future__ import annotations

from enum import Enum


class LeaderboardStatus(str, Enum):
    LIVE = "live"
    MAINTENANCE = "maintenance"
    OFFLINE = "offline"


_STATUS_TEXT: dict[LeaderboardStatus, str] = {
    LeaderboardStatus.LIVE: "🟢 Live",
    LeaderboardStatus.MAINTENANCE: "🟠 Paused | Under Maintenance",
    LeaderboardStatus.OFFLINE: "🔴 Offline",
}


def render_leaderboard_status(status: LeaderboardStatus | str) -> str:
    if isinstance(status, LeaderboardStatus):
        return _STATUS_TEXT[status]

    normalized = status.strip().lower()
    for candidate, text in _STATUS_TEXT.items():
        if normalized == candidate.value:
            return text
        if status == text:
            return text
    return status
