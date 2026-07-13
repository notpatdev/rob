from __future__ import annotations

import asyncio
from types import SimpleNamespace

from ops.ops import build_parser, handle_throne


class _FakeThroneCreators:
    def __init__(self):
        self.last_upsert = None

    async def get_by_user_id(self, guild_id: int, discord_user_id: int):
        if guild_id != 1 or discord_user_id != 10:
            return None
        return SimpleNamespace(
            guild_id=1,
            domme_id=5,
            discord_user_id=10,
            throne_handle="pat",
            throne_creator_id="creator-1",
            hide_own_purchases=False,
            tracking_mode="disabled",
            webhook_secret="old",
            webhook_secret_hash="old-hash",
            webhook_connected_at=None,
            last_successful_event_at=None,
            setup_verified_at=None,
        )

    async def upsert_for_user(self, **kwargs):
        self.last_upsert = kwargs
        return SimpleNamespace(
            throne_creator_id=kwargs["throne_creator_id"],
        )


class _FakeSendService:
    async def record_manual_send(self, **kwargs):
        return SimpleNamespace(id=88, public_send_id="ROB-000088-ABCDEF12", discord_post_status="pending")


class _FakeDommes:
    async def get_by_user_id(self, guild_id: int, user_id: int):
        if guild_id == 1 and user_id == 10:
            return SimpleNamespace(id=5)
        return None


class _FakeSends:
    async def list_sends_for_domme(self, guild_id: int, user_id: int, *, limit: int):
        del guild_id, user_id, limit
        return []


class _FakeSubs:
    async def upsert(self, *, guild_id: int, discord_user_id: int, send_name: str):
        return SimpleNamespace(id=3, guild_id=guild_id, discord_user_id=discord_user_id, send_name=send_name)


class _FakeRegistrationService:
    async def register_domme(self, *, guild_id: int, discord_user_id: int, throne_input: str):
        del throne_input
        return SimpleNamespace(
            domme=SimpleNamespace(id=7),
            creator=SimpleNamespace(throne_handle="pat", throne_creator_id="creator-1", tracking_mode="disabled"),
            webhook_url=f"https://example.test/throne/webhook/creator-1/{discord_user_id}",
        )


def _ctx():
    return SimpleNamespace(
        throne_creators=_FakeThroneCreators(),
        send_service=_FakeSendService(),
        dommes=_FakeDommes(),
        sends=_FakeSends(),
        subs=_FakeSubs(),
        registration_service=_FakeRegistrationService(),
        settings=SimpleNamespace(throne_test_gifter_usernames=("marie_123",)),
        guild_settings=SimpleNamespace(list_guild_ids=lambda: [1]),
    )


def test_parser_accepts_legacy_style_throne_subcommands():
    parser = build_parser()
    args = parser.parse_args(["throne", "search", "<@10>", "--guild-id", "1"])
    assert args.command == "throne"
    assert args.throne_command == "search"
    assert args.user_ref == "<@10>"

    args = parser.parse_args(["throne", "webhook", "refresh", "10", "--guild-id", "1"])
    assert args.throne_command == "webhook"
    assert args.throne_webhook_command == "refresh"

    args = parser.parse_args(["throne", "addsend", "10", "50.25", "--sub-name", "marie_123"])
    assert args.throne_command == "addsend"
    assert args.sub_name == "marie_123"

    args = parser.parse_args(["throne", "addsub", "10", "marie_123"])
    assert args.throne_command == "addsub"

    args = parser.parse_args(["throne", "adddomme", "10", "https://throne.com/pat"])
    assert args.throne_command == "adddomme"


def test_throne_addsend_records_manual_send(capsys):
    ctx = _ctx()
    args = SimpleNamespace(
        throne_command="addsend",
        user_ref="10",
        amount=50.25,
        guild_id=1,
        sub_name="marie_123",
        method="cashapp",
        currency="USD",
        note="manual",
    )

    asyncio.run(handle_throne(ctx, args))
    out = capsys.readouterr().out
    assert "Throne Add Send" in out
    assert "Recorded: true" in out
    assert "Public Send ID: ROB-000088-ABCDEF12" in out


def test_throne_webhook_refresh_rotates_secret(capsys, monkeypatch):
    monkeypatch.setenv("THRONE_WEBHOOK_BASE_URL", "https://rob.example.test")
    ctx = _ctx()
    args = SimpleNamespace(
        throne_command="webhook",
        throne_webhook_command="refresh",
        user_ref="<@10>",
        guild_id=1,
    )

    asyncio.run(handle_throne(ctx, args))
    out = capsys.readouterr().out
    assert "Throne Webhook Refresh" in out
    assert "Rotated: true" in out
    assert "Webhook URL: https://rob.example.test/throne/webhook/creator-1/" in out


def test_throne_adddomme_registers_via_service(capsys):
    ctx = _ctx()
    args = SimpleNamespace(
        throne_command="adddomme",
        guild_id=1,
        user_ref="10",
        throne_input="https://throne.com/pat",
    )

    asyncio.run(handle_throne(ctx, args))
    out = capsys.readouterr().out
    assert "Throne Add Dom/me" in out
    assert "Creator ID: creator-1" in out
    assert "Handle: @pat" in out
