from __future__ import annotations

import asyncio
from types import SimpleNamespace

from ops.ops import build_parser, handle_leaderboard


class _FakeLeaderboards:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def upsert_message(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(**kwargs)


def test_leaderboard_adopt_parser_accepts_expected_flags():
    parser = build_parser()
    args = parser.parse_args(
        [
            "leaderboard",
            "adopt",
            "--guild-id",
            "1",
            "--leaderboard-channel-id",
            "222",
            "--leaderboard-message-id",
            "333",
            "--stats-message-id",
            "444",
        ]
    )
    assert args.command == "leaderboard"
    assert args.leaderboard_command == "adopt"
    assert args.guild_id == 1
    assert args.leaderboard_channel_id == 222
    assert args.leaderboard_message_id == 333
    assert args.stats_message_id == 444


def test_leaderboard_adopt_writes_message_refs(capsys):
    leaderboards = _FakeLeaderboards()
    ctx = SimpleNamespace(leaderboards=leaderboards)
    args = SimpleNamespace(
        leaderboard_command="adopt",
        guild_id=1,
        leaderboard_channel_id=222,
        leaderboard_message_id=333,
        stats_message_id=444,
    )

    asyncio.run(handle_leaderboard(ctx, args))

    assert leaderboards.calls == [
        {
            "guild_id": 1,
            "message_key": "leaderboard",
            "leaderboard_type": "discord",
            "channel_id": 222,
            "message_id": 333,
        },
        {
            "guild_id": 1,
            "message_key": "leaderboard_stats",
            "leaderboard_type": "discord",
            "channel_id": 222,
            "message_id": 444,
        },
    ]
    output = capsys.readouterr().out
    assert "Leaderboard Adopt" in output
    assert "Leaderboard Message ID: 333" in output
    assert "Stats Message ID: 444" in output
