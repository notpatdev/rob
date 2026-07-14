from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from rob.database.repositories.public_sends import (
    PublicSendRow,
    PublicSendsRepository,
    _resolve_public_send_id,
)
from rob.utils.send_ids import build_public_send_id


def _row(**overrides) -> dict:
    now = datetime(2026, 7, 10, tzinfo=timezone.utc)
    base = {
        "id": 3,
        "guild_id": 1485460387355820034,
        "public_send_id": "ROB-000003-ABCD1234",
        "event_id": "evt_3",
        "fallback_event_hash": None,
        "created_at": now,
        "domme_user_id": 10,
        "sent_at": now,
        "amount_cents": 2500,
        "currency": "USD",
        "item_name": "Coffee",
        "sub_name": "Someone",
        "domme_display_name": "Miss X",
    }
    base.update(overrides)
    return base


class _FakeConnection:
    def __init__(self, rows, *, fetchrow_result=None):
        self.rows = rows
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []
        self.fetchrow_result = fetchrow_result

    async def fetch(self, query: str, *params):
        self.fetch_calls.append((query, params))
        return list(self.rows)

    async def fetchrow(self, query: str, *params):
        self.fetchrow_calls.append((query, params))
        return self.fetchrow_result


class _FakeDatabase:
    def __init__(self, rows, *, fetchrow_result=None):
        self.connection = _FakeConnection(rows, fetchrow_result=fetchrow_result)

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


def test_query_scopes_and_filters_counted_sends():
    db = _FakeDatabase([_row()])
    repo = PublicSendsRepository(db)

    result = asyncio.run(
        repo.counted_sends_for_username(guild_id=1485460387355820034, username="someone")
    )

    query, params = db.connection.fetch_calls[0]
    # Guild + username are bound parameters (no string interpolation of input).
    assert params == (1485460387355820034, "someone")
    # The counted-send filter is applied in SQL.
    assert "discord_post_status = 'posted'" in query
    assert "is_private = false" in query
    assert "is_test_send IS NOT TRUE" in query
    # Case-insensitive match on sub_name (sender) OR the Dom/me's throne_handle
    # (recipient), and newest-first ordering.
    assert "lower(s.sub_name) = lower($2)" in query
    assert "lower(d2.throne_handle) = lower($2)" in query
    assert "s.domme_id IN (" in query
    assert "ORDER BY s.sent_at DESC, s.id DESC" in query

    assert len(result) == 1
    assert isinstance(result[0], PublicSendRow)
    assert result[0].public_send_id == "ROB-000003-ABCD1234"
    assert result[0].domme_display_name == "Miss X"
    assert result[0].sub_name == "Someone"


def test_domme_label_for_username_returns_label_when_found():
    db = _FakeDatabase([], fetchrow_result={"label": "Miss X"})
    repo = PublicSendsRepository(db)
    label = asyncio.run(
        repo.domme_label_for_username(guild_id=1485460387355820034, username="missx")
    )
    assert label == "Miss X"
    query, params = db.connection.fetchrow_calls[0]
    assert params == (1485460387355820034, "missx")
    assert "FROM dommes" in query
    assert "lower(d.throne_handle) = lower($2)" in query


def test_domme_label_for_username_returns_none_when_not_a_domme():
    db = _FakeDatabase([], fetchrow_result=None)
    repo = PublicSendsRepository(db)
    label = asyncio.run(
        repo.domme_label_for_username(guild_id=1485460387355820034, username="some_sub")
    )
    assert label is None


def test_no_matches_returns_empty_list():
    db = _FakeDatabase([])
    repo = PublicSendsRepository(db)
    result = asyncio.run(
        repo.counted_sends_for_username(guild_id=1485460387355820034, username="nobody")
    )
    assert result == []


def test_public_send_id_fallback_matches_persisted_scheme():
    # A row missing public_send_id gets a deterministic id identical to the one
    # the bot would persist (build_public_send_id).
    row = _row(public_send_id=None)

    expected = build_public_send_id(
        _FakeSend(
            id=row["id"],
            guild_id=row["guild_id"],
            event_id=row["event_id"],
            fallback_event_hash=row["fallback_event_hash"],
            created_at=row["created_at"],
            domme_user_id=row["domme_user_id"],
            amount_cents=row["amount_cents"],
            sent_at=row["sent_at"],
        )
    )
    assert _resolve_public_send_id(row) == expected


def test_public_send_id_prefers_stored_value():
    assert _resolve_public_send_id(_row(public_send_id="ROB-000009-DEADBEEF")) == (
        "ROB-000009-DEADBEEF"
    )


class _FakeSend:
    """Minimal stand-in with the attributes build_public_send_id reads."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)
