from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from rob.database.repositories.leaderboards import LeaderboardsRepository


class _FakeConnection:
    def __init__(self, *, fetch_rows=None, fetchrow_row=None):
        self.fetch_rows = fetch_rows or []
        self.fetchrow_row = fetchrow_row
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.fetchrow_calls: list[tuple[str, tuple]] = []

    async def fetch(self, query: str, *params):
        self.fetch_calls.append((query, params))
        return self.fetch_rows

    async def fetchrow(self, query: str, *params):
        self.fetchrow_calls.append((query, params))
        return self.fetchrow_row


class _FakeDatabase:
    def __init__(self, connection: _FakeConnection):
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


def test_registered_domme_with_zero_total_is_mapped_from_left_join_query():
    connection = _FakeConnection(
        fetch_rows=[
            {"user_id": 123, "total_cents": 0, "send_count": 0},
            {"user_id": 456, "total_cents": 1099, "send_count": 1},
        ]
    )
    repo = LeaderboardsRepository(_FakeDatabase(connection))

    entries = asyncio.run(
        repo.get_top_dommes(
            1,
            limit=10,
            include_test_sends=False,
            test_gifter_usernames=("marie_123",),
            owner_test_user_id=None,
        )
    )

    query, params = connection.fetch_calls[0]
    assert "FROM dommes d" in query
    assert "LEFT JOIN valid_sends v" in query
    assert "is_private = false" in query
    assert "is_test_send" in query
    assert params == (1, False, ["marie_123"], None, 10)
    assert entries[0].label == "<@123>"
    assert entries[0].total_cents == 0
    assert entries[0].send_count == 0
    assert entries[1].total_cents == 1099


def test_summary_counts_registered_dommes_and_unclaimed_totals():
    connection = _FakeConnection(
        fetchrow_row={
            "total_cents": 5000,
            "send_count": 4,
            "domme_count": 3,
            "sub_count": 2,
            "unclaimed_send_count": 1,
            "unclaimed_total_cents": 1099,
        }
    )
    repo = LeaderboardsRepository(_FakeDatabase(connection))

    summary = asyncio.run(
        repo.get_summary(
            1,
            include_test_sends=False,
            test_gifter_usernames=("marie_123",),
            owner_test_user_id=None,
        )
    )

    query, params = connection.fetchrow_calls[0]
    assert "COUNT(*) FROM dommes" in query
    assert "valid_sends" in query
    assert params == (1, False, ["marie_123"], None)
    assert summary.domme_count == 3
    assert summary.send_count == 4
    assert summary.unclaimed_send_count == 1
    assert summary.unclaimed_total_cents == 1099


def test_message_queries_use_vib_leaderboard_table():
    connection = _FakeConnection(fetchrow_row=None)
    repo = LeaderboardsRepository(_FakeDatabase(connection))

    asyncio.run(repo.get_message(1, "leaderboard"))
    query, params = connection.fetchrow_calls[0]

    assert "FROM vib_leaderboard" in query
    assert "leaderboard_message" not in query
    assert params == (1, "leaderboard")


def test_message_upserts_use_vib_leaderboard_table():
    connection = _FakeConnection(
        fetchrow_row={
            "id": 1,
            "guild_id": 1,
            "leaderboard_key": "leaderboard",
            "leaderboard_type": "dommes",
            "channel_id": 123,
            "message_id": 456,
            "created_at": None,
            "updated_at": None,
        }
    )
    repo = LeaderboardsRepository(_FakeDatabase(connection))

    asyncio.run(
        repo.upsert_message(
            guild_id=1,
            message_key="leaderboard",
            leaderboard_type="dommes",
            channel_id=123,
            message_id=456,
        )
    )
    query, params = connection.fetchrow_calls[0]

    assert "INSERT INTO vib_leaderboard" in query
    assert "leaderboard_message" not in query
    assert params == (1, "leaderboard", "dommes", 123, 456)


def test_sub_stats_query_counts_by_sub_user_id_without_source_filter():
    connection = _FakeConnection(
        fetchrow_row={
            "total_cents": 3300,
            "send_count": 2,
        }
    )
    repo = LeaderboardsRepository(_FakeDatabase(connection))

    summary = asyncio.run(
        repo.get_sub_stats(
            1,
            sub_user_id=20,
            include_test_sends=False,
            test_gifter_usernames=("marie_123",),
            owner_test_user_id=None,
        )
    )

    query, params = connection.fetchrow_calls[0]
    assert "FROM valid_sends" in query
    assert "WHERE sub_user_id = $5" in query
    assert "source =" not in query
    assert params == (1, False, ["marie_123"], None, 20)
    assert summary.total_cents == 3300
    assert summary.send_count == 2


def test_public_data_freshness_query_does_not_reference_updated_at_and_uses_existing_timestamps():
    latest = object()
    connection = _FakeConnection(fetchrow_row={"latest_counted_send_at": latest})
    repo = LeaderboardsRepository(_FakeDatabase(connection))

    freshness = asyncio.run(
        repo.get_public_data_freshness(
            1,
            include_test_sends=False,
            test_gifter_usernames=("marie_123",),
            owner_test_user_id=None,
        )
    )

    query, params = connection.fetchrow_calls[0]
    assert "updated_at" not in query
    assert "COALESCE(v.sent_at, v.created_at)" in query
    assert params == (1, False, ["marie_123"], None)
    assert freshness is latest
