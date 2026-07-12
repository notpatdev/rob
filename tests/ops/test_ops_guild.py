from __future__ import annotations

import asyncio
from types import SimpleNamespace

from ops.ops import (
    LiveGuildChannel,
    LiveGuildRole,
    LiveGuildScanResult,
    build_parser,
    fetch_live_guild_scan,
    handle_guild,
)


class _FakeGuildSettings:
    def __init__(self):
        self.channel_calls: list[tuple[int, str, int | None]] = []
        self.role_calls: list[tuple[int, str, int | None]] = []

    async def get(self, guild_id: int):
        assert guild_id == 1
        return SimpleNamespace(
            guild_id=1,
            registration_channel_id=None,
            leaderboard_channel_id=22,
            send_track_channel_id=None,
            counting_channel_id=None,
            report_channel_id=None,
            warn_log_channel_id=None,
            domme_role_id=None,
            sub_role_id=None,
            mod_role_id=77,
            inactive_role_id=None,
        )

    async def set_channel_id(self, guild_id: int, field_name: str, channel_id: int | None):
        self.channel_calls.append((guild_id, field_name, channel_id))
        return SimpleNamespace(**{field_name: channel_id})

    async def set_role_id(self, guild_id: int, field_name: str, role_id: int | None):
        self.role_calls.append((guild_id, field_name, role_id))
        return SimpleNamespace(**{field_name: role_id})


def test_guild_parser_accepts_scan_set_channel_and_set_role():
    parser = build_parser()

    args = parser.parse_args(["guild", "scan", "--guild-id", "1"])
    assert args.command == "guild"
    assert args.guild_command == "scan"
    assert args.guild_id == 1

    args = parser.parse_args(
        [
            "guild",
            "set-channel",
            "--guild-id",
            "1",
            "--field",
            "leaderboard_channel_id",
            "--channel-id",
            "222",
        ]
    )
    assert args.command == "guild"
    assert args.guild_command == "set-channel"
    assert args.field == "leaderboard_channel_id"
    assert args.channel_id == 222

    args = parser.parse_args(
        [
            "guild",
            "set-role",
            "--guild-id",
            "1",
            "--field",
            "domme_role_id",
            "--role-id",
            "444",
        ]
    )
    assert args.command == "guild"
    assert args.guild_command == "set-role"
    assert args.field == "domme_role_id"
    assert args.role_id == 444


def test_guild_scan_reports_suggested_commands(capsys, monkeypatch):
    async def _fake_live_scan(_guild_id: int):
        return LiveGuildScanResult(
            guild_id=1,
            guild_name="Rob Test Server",
            channels=(
                LiveGuildChannel(channel_id=22, name="leaderboard", kind="TextChannel"),
                LiveGuildChannel(channel_id=33, name="send-tracker", kind="TextChannel"),
                LiveGuildChannel(channel_id=44, name="counting", kind="TextChannel"),
            ),
            roles=(
                LiveGuildRole(role_id=77, name="Moderator"),
                LiveGuildRole(role_id=88, name="Dom/me"),
                LiveGuildRole(role_id=99, name="Sub"),
            ),
            source="bot-session",
        )

    monkeypatch.setattr("ops.ops.fetch_live_guild_scan", _fake_live_scan)
    ctx = SimpleNamespace(guild_settings=_FakeGuildSettings())
    args = SimpleNamespace(guild_command="scan", guild_id=1)

    asyncio.run(handle_guild(ctx, args))

    out = capsys.readouterr().out
    assert "Guild Scan" in out
    assert "Live Source" in out
    assert "Leaderboard Channel:" in out
    assert "current: #leaderboard (22)" in out
    assert "Send Tracker Channel:" in out
    assert "suggested: #send-tracker (33)" in out
    assert "rob guild set-channel --guild-id 1 --field send_track_channel_id --channel-id 33" in out
    assert "Dom/me Role:" in out
    assert "suggested: @Dom/me (88)" in out
    assert "rob guild set-role --guild-id 1 --field domme_role_id --role-id 88" in out


def test_guild_set_channel_updates_db(capsys):
    guild_settings = _FakeGuildSettings()
    ctx = SimpleNamespace(guild_settings=guild_settings)
    args = SimpleNamespace(
        guild_command="set-channel",
        guild_id=1,
        field="leaderboard_channel_id",
        channel_id=222,
        clear=False,
    )

    asyncio.run(handle_guild(ctx, args))

    assert guild_settings.channel_calls == [(1, "leaderboard_channel_id", 222)]
    out = capsys.readouterr().out
    assert "Guild Channel Updated" in out
    assert "Field: leaderboard_channel_id" in out


def test_guild_set_role_updates_db(capsys):
    guild_settings = _FakeGuildSettings()
    ctx = SimpleNamespace(guild_settings=guild_settings)
    args = SimpleNamespace(
        guild_command="set-role",
        guild_id=1,
        field="domme_role_id",
        role_id=444,
        clear=False,
    )

    asyncio.run(handle_guild(ctx, args))

    assert guild_settings.role_calls == [(1, "domme_role_id", 444)]
    out = capsys.readouterr().out
    assert "Guild Role Updated" in out
    assert "Field: domme_role_id" in out


def test_live_guild_scan_prefers_running_bot_session(monkeypatch):
    async def _fake_bot_scan(guild_id: int):
        assert guild_id == 1
        return LiveGuildScanResult(
            guild_id=1,
            guild_name="Rob Test Server",
            channels=(),
            roles=(),
            source="bot-session",
        )

    async def _fake_rest_scan(_guild_id: int):
        raise AssertionError("REST fallback should not run when bot session scan succeeds.")

    monkeypatch.setattr("ops.ops.fetch_live_guild_scan_from_bot_ops", _fake_bot_scan)
    monkeypatch.setattr("ops.ops.fetch_live_guild_scan_from_discord_rest", _fake_rest_scan)

    result = asyncio.run(fetch_live_guild_scan(1))

    assert result.source == "bot-session"


def test_live_guild_scan_falls_back_to_rest_when_bot_endpoint_is_unavailable(monkeypatch):
    async def _fake_bot_scan(guild_id: int):
        assert guild_id == 1
        return None

    async def _fake_rest_scan(guild_id: int):
        assert guild_id == 1
        return LiveGuildScanResult(
            guild_id=1,
            guild_name="Rob Test Server",
            channels=(),
            roles=(),
            source="discord-rest",
        )

    monkeypatch.setattr("ops.ops.fetch_live_guild_scan_from_bot_ops", _fake_bot_scan)
    monkeypatch.setattr("ops.ops.fetch_live_guild_scan_from_discord_rest", _fake_rest_scan)

    result = asyncio.run(fetch_live_guild_scan(1))

    assert result.source == "discord-rest"
