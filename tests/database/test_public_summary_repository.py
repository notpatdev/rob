from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from rob.database.repositories.public_summary import (
    GuildSummary,
    PublicSummaryRepository,
)

GUILD = 1485460387355820034
T1 = datetime(2026, 7, 8, tzinfo=timezone.utc)
T2 = datetime(2026, 7, 14, tzinfo=timezone.utc)


class _FakeConnection:
    def __init__(self, *, totals, top, domme_count, sub_count):
        self._totals = totals
        self._top = top
        self._domme_count = domme_count
        self._sub_count = sub_count
        self.fetch_queries: list[str] = []
        self.fetchval_queries: list[str] = []

    async def fetch(self, query: str, *params):
        self.fetch_queries.append(query)
        if "GROUP BY s.currency" in query:
            return list(self._totals)
        return list(self._top)

    async def fetchval(self, query: str, *params):
        self.fetchval_queries.append(query)
        if "FROM dommes" in query:
            return self._domme_count
        return self._sub_count


class _FakeDatabase:
    def __init__(self, connection):
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection


def _run():
    conn = _FakeConnection(
        totals=[
            {"currency": "USD", "count": 3, "amount_cents": 12500, "last_sent_at": T2},
            {"currency": "EUR", "count": 1, "amount_cents": 5000, "last_sent_at": T1},
        ],
        top=[
            {"domme_display_name": "Miss X", "currency": "USD", "amount_cents": 10000, "count": 2},
            {"domme_display_name": "Miss Y", "currency": "USD", "amount_cents": 2500, "count": 1},
        ],
        domme_count=42,
        sub_count=300,
    )
    repo = PublicSummaryRepository(_FakeDatabase(conn))
    summary = asyncio.run(repo.guild_summary(guild_id=GUILD))
    return summary, conn


def test_summary_aggregates_totals_counts_and_last_updated():
    summary, _ = _run()
    assert isinstance(summary, GuildSummary)
    assert summary.total_count == 4  # 3 + 1
    assert summary.last_updated == T2  # newest across currencies
    assert summary.domme_count == 42
    assert summary.sub_count == 300

    assert [(t.currency, t.amount_cents, t.count) for t in summary.totals] == [
        ("USD", 12500, 3),
        ("EUR", 5000, 1),
    ]
    assert [(r.domme_display_name, r.amount_cents) for r in summary.top_receivers] == [
        ("Miss X", 10000),
        ("Miss Y", 2500),
    ]


def test_summary_queries_apply_counted_filter_and_active_scopes():
    _, conn = _run()
    joined = "\n".join(conn.fetch_queries + conn.fetchval_queries)
    assert "discord_post_status = 'posted'" in joined
    assert "is_private = false" in joined
    assert "is_test_send IS NOT TRUE" in joined
    assert "leaderboard_visible = true" in joined
    assert "profile_status = 'active'" in joined
    # Top receivers are capped and never expose Discord ids.
    top_sql = next(q for q in conn.fetch_queries if "LIMIT 10" in q)
    assert "domme_user_id" not in top_sql
    assert "discord_user_id" not in top_sql


def test_summary_handles_empty_guild():
    conn = _FakeConnection(totals=[], top=[], domme_count=0, sub_count=0)
    repo = PublicSummaryRepository(_FakeDatabase(conn))
    summary = asyncio.run(repo.guild_summary(guild_id=GUILD))
    assert summary.total_count == 0
    assert summary.last_updated is None
    assert summary.totals == []
    assert summary.top_receivers == []
