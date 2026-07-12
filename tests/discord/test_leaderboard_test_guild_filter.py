"""Test-guild-only leaderboard filtering."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from rob.config.guilds import MAIN_GUILD_ID, TEST_GUILD_ID
from rob.services.leaderboard_service import LeaderboardService


class _FakeDommes:
    def __init__(self, dommes):
        self._dommes = dommes
        self.calls = 0

    async def list_for_guild(self, guild_id):
        self.calls += 1
        return [d for d in self._dommes if d.guild_id == guild_id]


def _entry(user_id, total=100, count=1):
    return SimpleNamespace(user_id=user_id, total_cents=total, send_count=count)


def _make_service(dommes_repo):
    return LeaderboardService(
        bot=SimpleNamespace(),
        guild_settings=SimpleNamespace(),
        leaderboards=SimpleNamespace(),
        bot_state=SimpleNamespace(),
        maintenance=SimpleNamespace(),
        dommes=dommes_repo,
    )


def test_filter_keeps_all_entries_in_test_guild():
    # The leaderboard-visibility opt-out was removed; everyone with sends now
    # appears, even in the test guild.
    dommes = _FakeDommes([
        SimpleNamespace(guild_id=TEST_GUILD_ID, discord_user_id=1),
        SimpleNamespace(guild_id=TEST_GUILD_ID, discord_user_id=2),
    ])
    service = _make_service(dommes)
    entries = [_entry(1), _entry(2), _entry(3)]

    result = asyncio.run(service._filter_entries_for_guild(TEST_GUILD_ID, entries))

    assert [e.user_id for e in result] == [1, 2, 3]
    assert dommes.calls == 0


def test_filter_is_noop_outside_test_guild():
    dommes = _FakeDommes([
        SimpleNamespace(guild_id=MAIN_GUILD_ID, discord_user_id=1, leaderboard_visible=False),
    ])
    service = _make_service(dommes)
    entries = [_entry(1), _entry(2)]

    result = asyncio.run(service._filter_entries_for_guild(MAIN_GUILD_ID, entries))

    assert result is entries  # untouched
    assert dommes.calls == 0


def test_filter_noop_when_dommes_repo_missing():
    service = _make_service(None)
    entries = [_entry(1)]
    result = asyncio.run(service._filter_entries_for_guild(TEST_GUILD_ID, entries))
    assert result is entries
