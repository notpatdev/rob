"""End-to-end tests for GET /public/sends via aiohttp's test client.

The database layer is replaced by a stub repository so we exercise routing,
validation, guild scoping, serialization, and CORS without a live Postgres.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from rob.config.guilds import MAIN_GUILD_ID
from rob.database.repositories.public_sends import PublicSendRow
from rob.publicapi import create_public_api_app

pytestmark = pytest.mark.asyncio

ALLOWED_ORIGIN = "https://robthebot.com"


def _rows() -> list[PublicSendRow]:
    return [
        PublicSendRow(
            public_send_id="ROB-000003-ABCD1234",
            sent_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
            amount_cents=5000,
            currency="USD",
            domme_display_name="Miss X",
            item_name="Coffee",
            sub_name="Someone",
        ),
        PublicSendRow(
            public_send_id="ROB-000001-CCCC3333",
            sent_at=datetime(2026, 7, 8, tzinfo=timezone.utc),
            amount_cents=2500,
            currency="USD",
            domme_display_name="Miss Y",
            item_name=None,
            sub_name="someone",
        ),
    ]


class _StubRepo:
    def __init__(self, rows, *, domme_label=None):
        self._rows = rows
        self._domme_label = domme_label
        self.calls: list[dict] = []
        self.label_calls: list[dict] = []

    async def counted_sends_for_username(self, *, guild_id: int, username: str):
        self.calls.append({"guild_id": guild_id, "username": username})
        return list(self._rows)

    async def domme_label_for_username(self, *, guild_id: int, username: str):
        self.label_calls.append({"guild_id": guild_id, "username": username})
        return self._domme_label


class _FakeDatabase:
    async def health_check(self) -> bool:
        return True


def _make_client(rows, *, domme_label=None) -> tuple[TestServer, _StubRepo]:
    settings = SimpleNamespace(public_api_allowed_origin=ALLOWED_ORIGIN)
    app = create_public_api_app(settings=settings, database=_FakeDatabase())
    repo = _StubRepo(rows, domme_label=domme_label)
    app["public_sends_repository"] = repo
    return TestServer(app), repo


async def test_happy_path_returns_payload_and_cors():
    server, repo = _make_client(_rows())
    async with TestClient(server) as client:
        resp = await client.get("/public/sends", params={"username": "someone"})
        assert resp.status == 200
        assert resp.headers["Access-Control-Allow-Origin"] == ALLOWED_ORIGIN
        body = await resp.json()

    # Scoped to the main guild, username passed straight through.
    assert repo.calls == [{"guild_id": MAIN_GUILD_ID, "username": "someone"}]

    assert body["username"] == "someone"
    assert body["resolved_display_name"] == "Someone"  # newest row's stored casing
    assert body["last_updated"] == "2026-07-10T00:00:00Z"
    assert body["total_count"] == 2
    assert body["totals"] == [{"currency": "USD", "amount_cents": 7500, "count": 2}]
    assert len(body["recent"]) == 2
    assert len(body["all_sends"]) == 2

    row = body["all_sends"][0]
    assert row["public_send_id"] == "ROB-000003-ABCD1234"
    assert row["sent_at"] == "2026-07-10T00:00:00Z"


async def test_domme_username_resolves_display_name_from_domme_label():
    # A Dom/me (recipient): each row's sub_name is the *sender*, so the
    # resolved display name must come from the Dom/me label, not sub_name.
    rows = [
        PublicSendRow(
            public_send_id="ROB-000009-DDDD9999",
            sent_at=datetime(2026, 7, 11, tzinfo=timezone.utc),
            amount_cents=9000,
            currency="USD",
            domme_display_name="Miss X",
            item_name="Tribute",
            sub_name="a_generous_sub",
        )
    ]
    server, repo = _make_client(rows, domme_label="Miss X")
    async with TestClient(server) as client:
        resp = await client.get("/public/sends", params={"username": "missx"})
        assert resp.status == 200
        body = await resp.json()

    assert body["resolved_display_name"] == "Miss X"
    assert body["username"] == "missx"
    assert body["all_sends"][0]["sub_name"] == "a_generous_sub"
    # The domme label lookup was made with the main-guild scope.
    assert repo.label_calls == [{"guild_id": MAIN_GUILD_ID, "username": "missx"}]


async def test_send_rows_never_leak_discord_ids():
    server, _ = _make_client(_rows())
    async with TestClient(server) as client:
        resp = await client.get("/public/sends", params={"username": "someone"})
        body = await resp.json()

    allowed_keys = {
        "public_send_id",
        "sent_at",
        "amount_cents",
        "currency",
        "domme_display_name",
        "item_name",
        "sub_name",
    }
    for row in body["all_sends"] + body["recent"]:
        assert set(row.keys()) == allowed_keys
    # Belt and braces: no Discord-id-shaped fields anywhere in the response.
    import json

    raw = json.dumps(body)
    for forbidden in ("sub_user_id", "domme_user_id", "webhook_secret", "discord_"):
        assert forbidden not in raw


async def test_recent_is_capped_at_five():
    many = _rows() * 4  # 8 rows
    server, _ = _make_client(many)
    async with TestClient(server) as client:
        resp = await client.get("/public/sends", params={"username": "someone"})
        body = await resp.json()
    assert body["total_count"] == 8
    assert len(body["recent"]) == 5
    assert len(body["all_sends"]) == 8


async def test_missing_username_is_400():
    server, repo = _make_client(_rows())
    async with TestClient(server) as client:
        resp = await client.get("/public/sends")
        assert resp.status == 400
        assert resp.headers["Access-Control-Allow-Origin"] == ALLOWED_ORIGIN
    assert repo.calls == []  # never hit the DB


async def test_blank_username_is_400():
    server, _ = _make_client(_rows())
    async with TestClient(server) as client:
        resp = await client.get("/public/sends", params={"username": "   "})
        assert resp.status == 400


async def test_no_matches_is_404_with_cors():
    server, _ = _make_client([])
    async with TestClient(server) as client:
        resp = await client.get("/public/sends", params={"username": "nobody"})
        assert resp.status == 404
        assert resp.headers["Access-Control-Allow-Origin"] == ALLOWED_ORIGIN
        body = await resp.json()
        assert body["error"] == "not_found"


async def test_options_preflight_returns_204_with_cors():
    server, _ = _make_client(_rows())
    async with TestClient(server) as client:
        resp = await client.options("/public/sends")
        assert resp.status == 204
        assert resp.headers["Access-Control-Allow-Origin"] == ALLOWED_ORIGIN
        assert "GET" in resp.headers["Access-Control-Allow-Methods"]


async def test_health_endpoint():
    server, _ = _make_client(_rows())
    async with TestClient(server) as client:
        resp = await client.get("/health")
        assert resp.status == 200
        body = await resp.json()
        assert body["ok"] is True
