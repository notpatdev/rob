from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from rob.config.guilds import MAIN_GUILD_ID, TEST_GUILD_ID
from rob.discord.leaderboard_access import apply_leaderboard_access

# A guild that is neither main nor test: leaderboard access must still no-op.
OTHER_GUILD_ID = 424242424242424242


class _Role:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class _Member:
    def __init__(self, roles: list[_Role]) -> None:
        self.roles = roles
        self.add_roles = AsyncMock()
        self.remove_roles = AsyncMock()


class _Guild:
    def __init__(self, role: _Role | None, member: _Member | None) -> None:
        self._role = role
        self._member = member

    def get_role(self, role_id: int):
        if self._role is not None and self._role.id == role_id:
            return self._role
        return None

    def get_member(self, _user_id: int):
        return self._member


class _Bot:
    def __init__(self, *, role_id: int | None, guild: _Guild | None) -> None:
        self._guild = guild
        self.guild_settings_repo = SimpleNamespace(
            get=AsyncMock(
                return_value=SimpleNamespace(leaderboard_view_role_id=role_id)
            )
        )

    def get_guild(self, _guild_id: int):
        return self._guild


def test_enabled_assigns_role_when_missing():
    member = _Member(roles=[])
    bot = _Bot(role_id=99, guild=_Guild(_Role(99), member))
    ok = asyncio.run(
        apply_leaderboard_access(bot, guild_id=TEST_GUILD_ID, user_id=7, enabled=True)
    )
    assert ok is True
    member.add_roles.assert_awaited_once()
    member.remove_roles.assert_not_awaited()


def test_enabled_is_noop_when_role_present():
    member = _Member(roles=[_Role(99)])
    bot = _Bot(role_id=99, guild=_Guild(_Role(99), member))
    ok = asyncio.run(
        apply_leaderboard_access(bot, guild_id=TEST_GUILD_ID, user_id=7, enabled=True)
    )
    assert ok is True
    member.add_roles.assert_not_awaited()
    member.remove_roles.assert_not_awaited()


def test_disabled_removes_role_when_present():
    member = _Member(roles=[_Role(99)])
    bot = _Bot(role_id=99, guild=_Guild(_Role(99), member))
    ok = asyncio.run(
        apply_leaderboard_access(bot, guild_id=TEST_GUILD_ID, user_id=7, enabled=False)
    )
    assert ok is True
    member.remove_roles.assert_awaited_once()
    member.add_roles.assert_not_awaited()


def test_returns_false_when_role_not_configured():
    member = _Member(roles=[])
    bot = _Bot(role_id=None, guild=_Guild(None, member))
    ok = asyncio.run(
        apply_leaderboard_access(bot, guild_id=TEST_GUILD_ID, user_id=7, enabled=True)
    )
    assert ok is False
    member.add_roles.assert_not_awaited()


def test_assigns_role_in_main_guild():
    # The new system is now live on main: applying access must no longer no-op.
    member = _Member(roles=[])
    bot = _Bot(role_id=99, guild=_Guild(_Role(99), member))
    ok = asyncio.run(
        apply_leaderboard_access(bot, guild_id=MAIN_GUILD_ID, user_id=7, enabled=True)
    )
    assert ok is True
    bot.guild_settings_repo.get.assert_awaited()
    member.add_roles.assert_awaited_once()


def test_returns_false_outside_new_system_guild():
    member = _Member(roles=[])
    bot = _Bot(role_id=99, guild=_Guild(_Role(99), member))
    ok = asyncio.run(
        apply_leaderboard_access(bot, guild_id=OTHER_GUILD_ID, user_id=7, enabled=True)
    )
    assert ok is False
    # Should short-circuit before touching settings or roles.
    bot.guild_settings_repo.get.assert_not_awaited()
    member.add_roles.assert_not_awaited()
