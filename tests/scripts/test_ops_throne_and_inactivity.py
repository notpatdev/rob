from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from ops.ops import handle_throne


class _FakeThroneCreators:
    async def get_by_handle(self, _guild_id: int, handle: str):
        if handle.lower() != "pat":
            return None
        now = datetime.now(timezone.utc)
        return SimpleNamespace(
            throne_handle="pat",
            throne_creator_id="creator-1",
            discord_user_id=10,
            tracking_mode="webhook",
            webhook_connected_at=now,
            last_successful_event_at=now,
            setup_verified_at=now,
        )

    async def list_for_guild(self, _guild_id: int):
        return [
            SimpleNamespace(
                throne_handle="pat",
                throne_creator_id="creator-1",
                discord_user_id=10,
                tracking_mode="webhook",
                last_successful_event_at=None,
                setup_verified_at=None,
            )
        ]


class _FakeSubs:
    async def list_for_guild(self, _guild_id: int):
        now = datetime.now(timezone.utc)
        return [SimpleNamespace(discord_user_id=20, send_name="subby", registered_at=now)]


def test_throne_status_and_dommes_render(capsys):
    ctx = SimpleNamespace(
        throne_creators=_FakeThroneCreators(),
        subs=_FakeSubs(),
        settings=SimpleNamespace(),
        guild_settings=SimpleNamespace(list_guild_ids=lambda: [1]),
    )

    status_args = SimpleNamespace(throne_command="status", guild_id=1, handle="pat")
    asyncio.run(handle_throne(ctx, status_args))
    status_out = capsys.readouterr().out
    assert "Throne Status" in status_out
    assert "Found: true" in status_out
    assert "Creator ID: creator-1" in status_out

    dommes_args = SimpleNamespace(throne_command="dommes", guild_id=1)
    asyncio.run(handle_throne(ctx, dommes_args))
    dommes_out = capsys.readouterr().out
    assert "Throne Dom/mes" in dommes_out
    assert "@pat" in dommes_out
    assert "Creator ID: creator-1" in dommes_out
