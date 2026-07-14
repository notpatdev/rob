from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from rob.discord.client import RobBot


class _FakeTree:
    def __init__(self):
        self.clear_calls: list[object] = []
        self.sync_calls: list[object | None] = []

    def clear_commands(self, *, guild=None):
        self.clear_calls.append(guild)

    async def sync(self, *, guild=None):
        self.sync_calls.append(guild)
        return [SimpleNamespace(name="achievements")]


def test_sync_pushes_main_and_test_guild_commands_then_global():
    from rob.config.guilds import MAIN_GUILD_ID, TEST_GUILD_ID

    tree = _FakeTree()
    fake_bot = SimpleNamespace(tree=tree)

    asyncio.run(RobBot._sync_application_commands(fake_bot))

    assert [getattr(guild, "id", None) for guild in tree.sync_calls] == [
        MAIN_GUILD_ID,
        TEST_GUILD_ID,
        None,
    ]
    assert tree.clear_calls == []


def test_sync_continues_when_a_guild_is_forbidden():
    # A 403 Missing Access on one guild (bot missing the applications.commands
    # scope there) must not crash startup — the other guild and the global
    # sync still run.
    from rob.config.guilds import MAIN_GUILD_ID, TEST_GUILD_ID

    class _ForbiddenOnMainTree:
        def __init__(self):
            self.sync_calls: list[int | None] = []

        def clear_commands(self, *, guild=None):
            pass

        async def sync(self, *, guild=None):
            guild_id = getattr(guild, "id", None)
            self.sync_calls.append(guild_id)
            if guild_id == MAIN_GUILD_ID:
                raise discord.Forbidden(
                    SimpleNamespace(status=403, reason="Forbidden"), "Missing Access"
                )
            return [SimpleNamespace(name="cmd")]

    tree = _ForbiddenOnMainTree()
    fake_bot = SimpleNamespace(tree=tree)

    # Must not raise despite the main-guild 403.
    asyncio.run(RobBot._sync_application_commands(fake_bot))

    # It still attempted the test guild and the global (None) sync.
    assert tree.sync_calls == [MAIN_GUILD_ID, TEST_GUILD_ID, None]


def _fake_interaction(*, guild_id=123, command_name="leaderboard"):
    response = SimpleNamespace(send_message=AsyncMock())
    return SimpleNamespace(
        user=SimpleNamespace(id=7),
        guild=SimpleNamespace(id=guild_id) if guild_id is not None else None,
        guild_id=guild_id,
        command=SimpleNamespace(qualified_name=command_name),
        response=response,
    )


def test_global_interaction_check_allows_normal_interaction():
    fake_bot = SimpleNamespace(
        blacklist_repo=SimpleNamespace(contains=AsyncMock(return_value=False)),
        maintenance_service=SimpleNamespace(is_rob_offline_for_guild=AsyncMock(return_value=False)),
    )
    interaction = _fake_interaction(command_name="leaderboard")

    allowed = asyncio.run(RobBot._global_interaction_check(fake_bot, interaction))

    assert allowed is True
    interaction.response.send_message.assert_not_awaited()


def test_global_interaction_check_blocks_blacklisted_user():
    fake_bot = SimpleNamespace(
        blacklist_repo=SimpleNamespace(contains=AsyncMock(return_value=True)),
        maintenance_service=SimpleNamespace(is_rob_offline_for_guild=AsyncMock(return_value=False)),
    )
    interaction = _fake_interaction(command_name="leaderboard")

    allowed = asyncio.run(RobBot._global_interaction_check(fake_bot, interaction))

    assert allowed is False
    interaction.response.send_message.assert_awaited_once()


def test_global_interaction_check_blocks_commands_while_rob_offline_except_add():
    fake_bot = SimpleNamespace(
        blacklist_repo=SimpleNamespace(contains=AsyncMock(return_value=False)),
        maintenance_service=SimpleNamespace(is_rob_offline_for_guild=AsyncMock(return_value=True)),
    )

    blocked = asyncio.run(
        RobBot._global_interaction_check(fake_bot, _fake_interaction(command_name="leaderboard"))
    )
    assert blocked is False

    allowed = asyncio.run(
        RobBot._global_interaction_check(fake_bot, _fake_interaction(command_name="add"))
    )
    assert allowed is True
