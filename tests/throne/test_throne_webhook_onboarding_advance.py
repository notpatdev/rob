"""Tests for the webhook -> onboarding auto-advance wiring.

These tests exercise ``handle_throne_webhook`` end-to-end via aiohttp's
test client, with the database / send pipeline replaced by lightweight
stubs and ``notify_bot_onboarding_webhook_verified`` patched so we can
assert exactly when (and with what arguments) the auto-advance
notification is fired.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from rob.config.guilds import MAIN_GUILD_ID, TEST_GUILD_ID
from rob.throne import webhooks as webhooks_mod

pytestmark = pytest.mark.asyncio


def _fake_settings(*, parse_test_as_real: bool = False):
    return SimpleNamespace(
        throne_webhook_require_signature=False,
        throne_public_key_pem=None,
        throne_webhook_debug_log_payload=False,
        throne_webhook_timestamp_header="X-Throne-Timestamp",
        throne_webhook_signature_header="X-Throne-Signature",
        throne_webhook_signed_message_format="{timestamp}.{body}",
        throne_webhook_max_timestamp_skew_seconds=300,
        throne_test_gifter_usernames=("marie_123",),
        throne_parse_test_sends_as_real_sends=parse_test_as_real,
        rob_bot_notify_url="http://bot-ops.local:8811/sends/process",
        rob_ops_secret="topsecret",
    )


def _fake_creator(*, guild_id: int = TEST_GUILD_ID):
    return SimpleNamespace(
        id=10,
        guild_id=guild_id,
        discord_user_id=42,
        webhook_secret="secret",
        webhook_secret_hash=None,
    )


class _FakeDommes:
    def __init__(self, creator):
        self._creator = creator
        self.mark_setup_verified = AsyncMock()
        self.touch_successful_event = AsyncMock()

    async def get_by_creator_id(self, _creator_id):
        return [self._creator]


class _FakeSendService:
    def __init__(self, *, send=None):
        self._send = send

    async def record_throne_send(self, **_kwargs):
        return self._send


def _build_test_app(
    *,
    monkeypatch: pytest.MonkeyPatch,
    settings,
    creator,
    send=None,
    notify_mock: AsyncMock | None = None,
):
    """Build a webhook aiohttp.Application with all DB/send/notify deps
    replaced by lightweight stubs.
    """

    notify_mock = notify_mock or AsyncMock(return_value=True)

    monkeypatch.setattr(
        webhooks_mod, "DommesRepository", lambda _db: _FakeDommes(creator)
    )
    monkeypatch.setattr(
        webhooks_mod, "SendsRepository", lambda _db: SimpleNamespace()
    )
    monkeypatch.setattr(
        webhooks_mod, "BotStateRepository", lambda _db: SimpleNamespace()
    )
    monkeypatch.setattr(
        webhooks_mod,
        "MaintenanceService",
        lambda _repo: SimpleNamespace(),
    )
    monkeypatch.setattr(
        webhooks_mod,
        "SendService",
        lambda **_kwargs: _FakeSendService(send=send),
    )
    monkeypatch.setattr(
        webhooks_mod, "notify_bot_send", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        webhooks_mod, "notify_bot_onboarding_webhook_verified", notify_mock
    )

    app = web.Application()
    app["settings"] = settings
    app["database"] = SimpleNamespace()
    app["throne_service"] = SimpleNamespace()
    app["subs_repository"] = SimpleNamespace()
    app.router.add_post(
        "/throne/webhook/{creator_id}/{secret}",
        webhooks_mod.handle_throne_webhook,
    )
    return app, notify_mock


def _post(client: TestClient, body: dict):
    return client.post("/throne/webhook/cid/secret", json=body)


# ---------------------------------------------------------------------------
# Explicit test webhook -> auto-advance fires
# ---------------------------------------------------------------------------


async def test_explicit_test_webhook_triggers_auto_advance(
    monkeypatch,
):
    settings = _fake_settings()
    creator = _fake_creator()
    app, notify_mock = _build_test_app(
        monkeypatch=monkeypatch, settings=settings, creator=creator
    )

    async with TestClient(TestServer(app)) as client:
        resp = await _post(client, {"type": "test_webhook", "data": {}})
        body = await resp.json()

        assert resp.status == 200
        assert body.get("setup_verified") is True
        notify_mock.assert_awaited_once()
        call = notify_mock.await_args
        assert call.kwargs["guild_id"] == TEST_GUILD_ID
        assert call.kwargs["discord_user_id"] == 42


# ---------------------------------------------------------------------------
# Known test sender (parse_test_as_real=False, the default)
# ---------------------------------------------------------------------------


async def test_known_test_sender_triggers_auto_advance(
    monkeypatch,
):
    settings = _fake_settings(parse_test_as_real=False)
    creator = _fake_creator()
    send = SimpleNamespace(id=99, guild_id=TEST_GUILD_ID)
    app, notify_mock = _build_test_app(
        monkeypatch=monkeypatch, settings=settings, creator=creator, send=send
    )

    async with TestClient(TestServer(app)) as client:
        resp = await _post(
            client,
            {
                "type": "gift_purchased",
                "data": {
                    "gifter": {"username": "marie_123"},
                    "orderId": "ord-1",
                    "price": "1.00",
                },
            },
        )
        body = await resp.json()

        assert resp.status == 200
        assert body.get("setup_verified") is True
        # The known-test-sender branch + the real-send branch are both
        # eligible to fire; the handler de-duplicates so exactly one
        # notification is sent per inbound webhook.
        notify_mock.assert_awaited_once()
        assert notify_mock.await_args.kwargs["guild_id"] == TEST_GUILD_ID
        assert notify_mock.await_args.kwargs["discord_user_id"] == 42


# ---------------------------------------------------------------------------
# Known test sender, parse_test_as_real=True -> still auto-advances
# (this is the case that previously silently dropped the auto-advance).
# ---------------------------------------------------------------------------


async def test_known_test_sender_parsed_as_real_triggers_auto_advance(
    monkeypatch,
):
    settings = _fake_settings(parse_test_as_real=True)
    creator = _fake_creator()
    send = SimpleNamespace(id=99, guild_id=TEST_GUILD_ID)
    app, notify_mock = _build_test_app(
        monkeypatch=monkeypatch, settings=settings, creator=creator, send=send
    )

    async with TestClient(TestServer(app)) as client:
        resp = await _post(
            client,
            {
                "type": "gift_purchased",
                "data": {
                    "gifter": {"username": "marie_123"},
                    "orderId": "ord-2",
                    "price": "1.00",
                },
            },
        )

        assert resp.status == 200
        notify_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# Regular real send for a Dom/me in test guild also advances onboarding
# (cog is a safe no-op if onboarding is already complete).
# ---------------------------------------------------------------------------


async def test_real_send_in_test_guild_triggers_auto_advance(
    monkeypatch,
):
    settings = _fake_settings()
    creator = _fake_creator()
    send = SimpleNamespace(id=99, guild_id=TEST_GUILD_ID)
    app, notify_mock = _build_test_app(
        monkeypatch=monkeypatch, settings=settings, creator=creator, send=send
    )

    async with TestClient(TestServer(app)) as client:
        resp = await _post(
            client,
            {
                "type": "gift_purchased",
                "data": {
                    "gifter": {"username": "real_user"},
                    "orderId": "ord-3",
                    "price": "5.00",
                },
            },
        )
        assert resp.status == 200
        notify_mock.assert_awaited_once()
        assert notify_mock.await_args.kwargs["guild_id"] == TEST_GUILD_ID


# ---------------------------------------------------------------------------
# Main-guild webhook now triggers auto-advance (new system is live on main)
# ---------------------------------------------------------------------------


async def test_main_guild_webhook_triggers_auto_advance(
    monkeypatch,
):
    settings = _fake_settings()
    creator = _fake_creator(guild_id=MAIN_GUILD_ID)
    send = SimpleNamespace(id=99, guild_id=MAIN_GUILD_ID)
    app, notify_mock = _build_test_app(
        monkeypatch=monkeypatch, settings=settings, creator=creator, send=send
    )

    async with TestClient(TestServer(app)) as client:
        resp = await _post(client, {"type": "test_webhook", "data": {}})
        assert resp.status == 200
        notify_mock.assert_awaited_once()
        assert notify_mock.await_args.kwargs["guild_id"] == MAIN_GUILD_ID
        assert notify_mock.await_args.kwargs["discord_user_id"] == 42


# ---------------------------------------------------------------------------
# A guild that is neither main nor test never triggers auto-advance
# ---------------------------------------------------------------------------


async def test_non_system_guild_webhook_does_not_trigger_auto_advance(
    monkeypatch,
):
    settings = _fake_settings()
    creator = _fake_creator(guild_id=424242424242424242)
    send = SimpleNamespace(id=99, guild_id=424242424242424242)
    app, notify_mock = _build_test_app(
        monkeypatch=monkeypatch, settings=settings, creator=creator, send=send
    )

    async with TestClient(TestServer(app)) as client:
        # Explicit test path
        resp1 = await _post(client, {"type": "test_webhook", "data": {}})
        assert resp1.status == 200
        # Real send path
        resp2 = await _post(
            client,
            {
                "type": "gift_purchased",
                "data": {
                    "gifter": {"username": "real_user"},
                    "orderId": "ord-4",
                    "price": "5.00",
                },
            },
        )
        assert resp2.status == 200
        notify_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Duplicate send (record_throne_send returns None) still advances
# ---------------------------------------------------------------------------


async def test_duplicate_send_still_triggers_auto_advance(
    monkeypatch,
):
    settings = _fake_settings()
    creator = _fake_creator()
    app, notify_mock = _build_test_app(
        monkeypatch=monkeypatch, settings=settings, creator=creator, send=None
    )

    async with TestClient(TestServer(app)) as client:
        resp = await _post(
            client,
            {
                "type": "gift_purchased",
                "data": {
                    "gifter": {"username": "real_user"},
                    "orderId": "ord-dup",
                    "price": "1.00",
                },
            },
        )
        body = await resp.json()
        assert resp.status == 200
        assert body.get("duplicate") is True
        notify_mock.assert_awaited_once()
