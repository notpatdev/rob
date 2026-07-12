from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from rob.database.repositories.models import NewSend, SendRecord
from rob.database.repositories.sends import SendsRepository
from rob.utils.send_ids import build_public_send_id


def _row(
    *,
    send_id: int = 1,
    public_send_id: str | None = None,
    event_id: str | None = "evt_1",
) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "id": send_id,
        "guild_id": 1,
        "domme_id": None,
        "domme_user_id": 10,
        "sub_id": None,
        "sub_user_id": 20,
        "sub_name": "gifter_name",
        "amount_cents": 1099,
        "currency": "USD",
        "method": "throne",
        "source": "throne_webhook",
        "item_name": "Flowers",
        "item_image_url": None,
        "external_id": None,
        "event_id": event_id,
        "fallback_event_hash": "hash_1",
        "is_private": False,
        "seeded": False,
        "sent_at": now,
        "received_at": now,
        "discord_post_status": "pending",
        "discord_posted_at": None,
        "discord_message_id": None,
        "discord_post_error": None,
        "created_at": now,
        "is_test_send": False,
        "public_send_id": public_send_id,
    }


def _record_from_row(row: dict) -> SendRecord:
    return SendRecord(
        row["id"],
        row["guild_id"],
        row["domme_id"],
        row["domme_user_id"],
        row["sub_id"],
        row["sub_user_id"],
        row["sub_name"],
        row["amount_cents"],
        row["currency"],
        row["method"],
        row["source"],
        row["item_name"],
        row["item_image_url"],
        row["external_id"],
        row["event_id"],
        row["fallback_event_hash"],
        row["is_private"],
        row["seeded"],
        row["sent_at"],
        row["received_at"],
        row["discord_post_status"],
        row["discord_posted_at"],
        row["discord_message_id"],
        row["discord_post_error"],
        row["created_at"],
        row["is_test_send"],
    )


def _new_send() -> NewSend:
    now = datetime.now(timezone.utc)
    return NewSend(
        guild_id=1,
        domme_id=None,
        domme_user_id=10,
        sub_id=None,
        sub_user_id=20,
        sub_name="gifter_name",
        amount_cents=1099,
        currency="USD",
        method="throne",
        source="throne_webhook",
        item_name="Flowers",
        item_image_url=None,
        external_id=None,
        event_id="evt_1",
        fallback_event_hash="hash_1",
        is_private=False,
        seeded=False,
        sent_at=now,
        discord_post_status="pending",
        is_test_send=False,
    )


class _FakeConnection:
    def __init__(self, *, fetchrow_responses=None, fetch_responses=None, execute_responses=None):
        self.fetchrow_responses = list(fetchrow_responses or [])
        self.fetch_responses = list(fetch_responses or [])
        self.execute_responses = list(execute_responses or [])
        self.fetchrow_calls: list[tuple[str, tuple]] = []
        self.fetch_calls: list[tuple[str, tuple]] = []
        self.execute_calls: list[tuple[str, tuple]] = []

    async def fetchrow(self, query: str, *params):
        self.fetchrow_calls.append((query, params))
        return self.fetchrow_responses.pop(0) if self.fetchrow_responses else None

    async def fetch(self, query: str, *params):
        self.fetch_calls.append((query, params))
        return self.fetch_responses.pop(0) if self.fetch_responses else []

    async def execute(self, query: str, *params):
        self.execute_calls.append((query, params))
        return self.execute_responses.pop(0) if self.execute_responses else "UPDATE 0"


class _FakeDatabase:
    def __init__(self, connection: _FakeConnection):
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection

    @asynccontextmanager
    async def transaction(self):
        yield self.connection


def test_insert_assigns_and_stores_public_send_id():
    inserted = _row(public_send_id=None)
    stored_id = build_public_send_id(_record_from_row(inserted))
    updated = {**inserted, "public_send_id": stored_id}
    connection = _FakeConnection(fetchrow_responses=[inserted, updated])
    repo = SendsRepository(_FakeDatabase(connection))

    send = asyncio.run(repo.insert(_new_send()))

    assert send is not None
    assert send.stored_public_send_id == stored_id
    assert "UPDATE sends" in connection.fetchrow_calls[1][0]


def test_get_by_public_id_returns_matching_send_from_stored_column():
    row = _row(public_send_id="ROB-000001-ABCDEF12")
    connection = _FakeConnection(fetchrow_responses=[row])
    repo = SendsRepository(_FakeDatabase(connection))

    send = asyncio.run(repo.get_by_public_id("ROB-000001-ABCDEF12"))

    assert send is not None
    assert send.stored_public_send_id == "ROB-000001-ABCDEF12"
    assert "WHERE public_send_id = $1" in connection.fetchrow_calls[0][0]


def test_backfill_public_send_ids_updates_missing_rows():
    missing = _row(send_id=1, public_send_id=None, event_id="evt_1")
    missing_two = _row(send_id=2, public_send_id=None, event_id="evt_2")
    updated_one = {**missing, "public_send_id": build_public_send_id(_record_from_row(missing))}
    updated_two = {**missing_two, "public_send_id": build_public_send_id(_record_from_row(missing_two))}
    connection = _FakeConnection(
        fetch_responses=[[missing, missing_two]],
        fetchrow_responses=[updated_one, updated_two],
    )
    repo = SendsRepository(_FakeDatabase(connection))

    updated = asyncio.run(repo.backfill_public_send_ids())

    assert updated == 2
    assert len(connection.fetchrow_calls) == 2


def test_repair_mention_sub_links_dry_run_counts_without_writing():
    connection = _FakeConnection(fetch_responses=[[{"id": 1}, {"id": 2}]])
    repo = SendsRepository(_FakeDatabase(connection))

    candidates, updated = asyncio.run(repo.repair_mention_sub_links(dry_run=True))

    assert candidates == 2
    assert updated == 0
    # No UPDATE is issued on a dry run.
    assert connection.execute_calls == []
    # All-guilds scope passes NULL for the guild filter param.
    select_query, select_params = connection.fetch_calls[0]
    assert "sub_user_id IS NULL" in select_query
    assert select_params == (r"^<@!?[0-9]+>$", None)


def test_repair_mention_sub_links_applies_update_and_returns_count():
    connection = _FakeConnection(
        fetch_responses=[[{"id": 5}, {"id": 6}, {"id": 7}]],
        execute_responses=["UPDATE 3"],
    )
    repo = SendsRepository(_FakeDatabase(connection))

    candidates, updated = asyncio.run(
        repo.repair_mention_sub_links(guild_id=42, dry_run=False)
    )

    assert candidates == 3
    assert updated == 3
    assert len(connection.execute_calls) == 1
    update_query, update_params = connection.execute_calls[0]
    assert "UPDATE sends" in update_query
    # Restores registered send names via the subs join, links unregistered users too.
    assert "LEFT JOIN subs" in update_query
    assert "substring(sub_name from '^<@!?([0-9]+)>$')" in update_query
    assert update_params == (r"^<@!?[0-9]+>$", 42)


def test_repair_mention_sub_links_skips_update_when_no_candidates():
    connection = _FakeConnection(fetch_responses=[[]])
    repo = SendsRepository(_FakeDatabase(connection))

    candidates, updated = asyncio.run(repo.repair_mention_sub_links(dry_run=False))

    assert candidates == 0
    assert updated == 0
    assert connection.execute_calls == []


def test_public_send_id_db_build_scripts_define_column_and_unique_index():
    core_schema = Path("db/build/001_core_schema.sql").read_text(encoding="utf-8")
    indexes = Path("db/build/002_indexes.sql").read_text(encoding="utf-8")

    assert "public_send_id TEXT" in core_schema
    assert "original_amount_cents INTEGER" in core_schema
    assert "original_currency TEXT" in core_schema
    assert "CREATE UNIQUE INDEX IF NOT EXISTS idx_sends_public_send_id_unique\nON sends (public_send_id);" in indexes
    assert "CREATE UNIQUE INDEX IF NOT EXISTS idx_sends_event_id_unique\nON sends (event_id);" in indexes
    assert "WHERE public_send_id IS NOT NULL" not in indexes
    assert "WHERE event_id IS NOT NULL" not in indexes
