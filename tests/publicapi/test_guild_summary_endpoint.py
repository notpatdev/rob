"""End-to-end tests for GET /public/guild-summary and CORS reflection."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from rob.config.guilds import MAIN_GUILD_ID
from rob.database.repositories.public_summary import (
    CurrencyTotal,
    GuildSummary,
    TopReceiver,
)
from rob.publicapi import create_public_api_app

pytestmark = pytest.mark.asyncio


def _summary() -> GuildSummary:
    return GuildSummary(
        last_updated=datetime(2026, 7, 14, 12, 34, 56, tzinfo=timezone.utc),
        total_count=1234,
        domme_count=42,
        sub_count=300,
        totals=[CurrencyTotal(currency="USD", amount_cents=1234567, count=1200)],
        top_receivers=[
            TopReceiver(
                domme_display_name="Miss X",
                amount_cents=500000,
                currency="USD",
                count=120,
            )
        ],
    )


class _StubSummaryRepo:
    def __init__(self, summary):
        self._summary = summary
        self.calls: list[int] = []

    async def guild_summary(self, *, guild_id: int):
        self.calls.append(guild_id)
        return self._summary


class _FakeDatabase:
    async def health_check(self) -> bool:
        return True


def _make_server(summary, *, allowed_origin="https://robthebot.com"):
    settings = SimpleNamespace(public_api_allowed_origin=allowed_origin)
    app = create_public_api_app(settings=settings, database=_FakeDatabase())
    repo = _StubSummaryRepo(summary)
    app["public_summary_repository"] = repo
    return TestServer(app), repo


async def test_guild_summary_payload_and_guild_scope():
    server, repo = _make_server(_summary())
    async with TestClient(server) as client:
        resp = await client.get("/public/guild-summary")
        assert resp.status == 200
        body = await resp.json()

    assert repo.calls == [MAIN_GUILD_ID]
    assert body == {
        "last_updated": "2026-07-14T12:34:56Z",
        "total_count": 1234,
        "domme_count": 42,
        "sub_count": 300,
        "totals": [{"currency": "USD", "amount_cents": 1234567, "count": 1200}],
        "top_receivers": [
            {
                "domme_display_name": "Miss X",
                "amount_cents": 500000,
                "currency": "USD",
                "count": 120,
            }
        ],
    }


async def test_empty_guild_summary_returns_zeros_and_null():
    empty = GuildSummary(
        last_updated=None,
        total_count=0,
        domme_count=0,
        sub_count=0,
        totals=[],
        top_receivers=[],
    )
    server, _ = _make_server(empty)
    async with TestClient(server) as client:
        resp = await client.get("/public/guild-summary")
        assert resp.status == 200
        body = await resp.json()
    assert body["last_updated"] is None
    assert body["total_count"] == 0
    assert body["totals"] == []


async def test_cors_reflects_preview_origin_when_wildcard_allowed():
    server, _ = _make_server(
        _summary(),
        allowed_origin="https://robthebot.com,https://*.lovableproject.com",
    )
    preview = "https://preview--rob.lovableproject.com"
    async with TestClient(server) as client:
        resp = await client.get(
            "/public/guild-summary", headers={"Origin": preview}
        )
        assert resp.status == 200
        assert resp.headers["Access-Control-Allow-Origin"] == preview
        assert resp.headers["Vary"] == "Origin"


async def test_cors_preflight_reflects_origin_and_allows_get():
    server, _ = _make_server(_summary(), allowed_origin="*")
    origin = "https://anything.example"
    async with TestClient(server) as client:
        resp = await client.options(
            "/public/guild-summary", headers={"Origin": origin}
        )
        assert resp.status == 204
        assert resp.headers["Access-Control-Allow-Origin"] == origin
        assert "GET" in resp.headers["Access-Control-Allow-Methods"]
