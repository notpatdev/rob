"""Guild id constants and gating helpers.

Test-guild-only features should gate on is_test_guild; other guilds keep the
existing main-server behavior.
"""

from __future__ import annotations

MAIN_GUILD_ID: int = 1485460387355820034
TEST_GUILD_ID: int = 1506597978251591813

# Bot-owner/operator Discord user ids; operational DMs such as /report go here.
# Deliberately not the application_info() owner, which can be a team account or
# have DMs closed.
BOT_OWNER_USER_IDS: tuple[int, ...] = (
    1299308718009356289,
    1298381529311088671,
)
OWNER_USER_ID: int = BOT_OWNER_USER_IDS[0]

# "DO NOT TOUCH" accounts: the inactivity system must never role-swap, DM, or
# kick these; they stay Active. Seeded with a deceased member kept as a memorial.
PROTECTED_USER_IDS: frozenset[int] = frozenset(
    {
        1455563825393832095,
    }
)


def is_test_guild(guild_id: int | None) -> bool:
    """True when guild_id matches the test guild; None returns False."""
    if guild_id is None:
        return False
    return int(guild_id) == TEST_GUILD_ID


def is_main_guild(guild_id: int | None) -> bool:
    """True when guild_id matches the production main guild."""
    if guild_id is None:
        return False
    return int(guild_id) == MAIN_GUILD_ID


def is_new_system_guild(guild_id: int | None) -> bool:
    """Guilds where the newer systems (Dom/me onboarding, leaderboard access,
    inactivity, hourly backups) are live: main + test."""
    if guild_id is None:
        return False
    return is_main_guild(guild_id) or is_test_guild(guild_id)


def is_protected_user(user_id: int | None) -> bool:
    """True for "DO NOT TOUCH" accounts exempt from member-lifecycle automations."""
    if user_id is None:
        return False
    return int(user_id) in PROTECTED_USER_IDS


__all__ = [
    "MAIN_GUILD_ID",
    "TEST_GUILD_ID",
    "BOT_OWNER_USER_IDS",
    "OWNER_USER_ID",
    "PROTECTED_USER_IDS",
    "is_test_guild",
    "is_main_guild",
    "is_new_system_guild",
    "is_protected_user",
]
