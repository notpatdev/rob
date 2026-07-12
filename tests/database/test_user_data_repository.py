"""Tests for UserDataRepository.

No Postgres here: a fake connection records every execute/fetch and returns
DELETE <n> command tags, so tests can assert which tables each erasure touches.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from rob.database.repositories.user_data import UserDataRepository, _rows_affected


class _FakeConnection:
    def __init__(self, *, fetch_responses=None):
        self.fetch_responses = list(fetch_responses or [])
        self.execute_calls: list[tuple[str, tuple]] = []
        self.fetch_calls: list[tuple[str, tuple]] = []
        # substring -> rows-deleted; default 1 for any DELETE
        self.delete_counts: dict[str, int] = {}

    async def execute(self, query: str, *params) -> str:
        self.execute_calls.append((query, params))
        count = 1
        for needle, value in self.delete_counts.items():
            if needle in query:
                count = value
                break
        return f"DELETE {count}"

    async def fetch(self, query: str, *params):
        self.fetch_calls.append((query, params))
        return self.fetch_responses.pop(0) if self.fetch_responses else []


class _FakeDatabase:
    def __init__(self, connection: _FakeConnection):
        self.connection = connection

    @asynccontextmanager
    async def acquire(self):
        yield self.connection

    @asynccontextmanager
    async def transaction(self):
        yield self.connection


def _queries(connection: _FakeConnection) -> str:
    return "\n".join(query for query, _params in connection.execute_calls)


# ---------------------------------------------------------------------------
# delete_user_everywhere
# ---------------------------------------------------------------------------


def test_delete_everywhere_touches_all_user_tables_and_returns_counts():
    connection = _FakeConnection()
    repo = UserDataRepository(_FakeDatabase(connection))

    deleted = asyncio.run(repo.delete_user_everywhere(555))

    assert set(deleted) == {
        "sub_send_names",
        "subs",
        "dommes",
        "sends",
        "count_blocks",
        "count_recovery_windows",
        "send_change_requests",
        "domme_onboarding_state",
        "bot_settings",
        "user_terms_acceptance",
        "inactive_users",
        "bot_users_pii_cleared",
        "the_count",
    }
    assert all(count == 1 for count in deleted.values())

    queries = _queries(connection)
    # bot_users is kept for block enforcement; its PII is nulled, never hard-deleted.
    assert "DELETE FROM bot_users" not in queries
    assert "UPDATE bot_users SET discord_username = NULL" in queries
    assert "UPDATE the_count SET last_user_id = NULL" in queries
    assert "DELETE FROM inactive_users WHERE discord_user_id = $1" in queries
    assert "DELETE FROM user_terms_acceptance WHERE discord_user_id = $1" in queries


def test_delete_everywhere_uses_or_clauses_for_dual_role_tables():
    connection = _FakeConnection()
    repo = UserDataRepository(_FakeDatabase(connection))

    asyncio.run(repo.delete_user_everywhere(555))
    queries = _queries(connection)

    # sends / count_recovery also match the FK id columns via subqueries on the
    # user's dommes/subs rows, so no child row referencing them survives.
    assert "DELETE FROM sends WHERE (domme_user_id = $1 OR sub_user_id = $1" in queries
    assert "OR domme_id IN (SELECT id FROM dommes WHERE discord_user_id = $1" in queries
    assert "OR sub_id IN (SELECT id FROM subs WHERE discord_user_id = $1" in queries
    assert (
        "DELETE FROM count_recovery_windows "
        "WHERE (failed_user_id = $1 OR required_domme_user_id = $1" in queries
    )
    assert (
        "OR required_domme_id IN (SELECT id FROM dommes WHERE discord_user_id = $1"
        in queries
    )
    assert (
        "DELETE FROM send_change_requests "
        "WHERE (domme_user_id = $1 OR approved_by_user_id = $1)" in queries
    )


def test_delete_removes_fk_children_before_parents():
    # sends.domme_id/sub_id and count_recovery_windows.required_domme_id are
    # RESTRICT FKs into dommes/subs; deleting the parent first raised
    # ForeignKeyViolationError in production.
    connection = _FakeConnection()
    repo = UserDataRepository(_FakeDatabase(connection))

    asyncio.run(repo.delete_user_in_guild(555, 4242))

    def idx(table: str) -> int:
        needle = f"DELETE FROM {table} "
        for i, (query, _params) in enumerate(connection.execute_calls):
            if needle in query:
                return i
        raise AssertionError(f"no DELETE FROM {table}")

    assert idx("sends") < idx("dommes")
    assert idx("sends") < idx("subs")
    assert idx("count_recovery_windows") < idx("dommes")
    assert idx("sub_send_names") < idx("subs")


def test_delete_everywhere_has_no_guild_filter_and_wildcards_bot_settings():
    connection = _FakeConnection()
    repo = UserDataRepository(_FakeDatabase(connection))

    asyncio.run(repo.delete_user_everywhere(555))

    assert " AND guild_id = $2" not in _queries(connection)

    settings_call = next(
        call for call in connection.execute_calls if "bot_settings" in call[0]
    )
    _query, params = settings_call
    assert params == ("activity:%:user:555:%", "inactivity:%:user:555:%")


def test_delete_everywhere_runs_in_single_transaction():
    # 13 = every per-table statement plus the terms purge, all on one connection.
    connection = _FakeConnection()
    repo = UserDataRepository(_FakeDatabase(connection))

    asyncio.run(repo.delete_user_everywhere(555))
    assert len(connection.execute_calls) == 13


# ---------------------------------------------------------------------------
# delete_user_in_guild
# ---------------------------------------------------------------------------


def test_delete_in_guild_constrains_every_statement_and_skips_terms():
    connection = _FakeConnection()
    repo = UserDataRepository(_FakeDatabase(connection))

    deleted = asyncio.run(repo.delete_user_in_guild(555, 4242))

    # Terms acceptance is global-only, never per-guild.
    assert "user_terms_acceptance" not in deleted
    assert set(deleted) == {
        "sub_send_names",
        "subs",
        "dommes",
        "sends",
        "count_blocks",
        "count_recovery_windows",
        "send_change_requests",
        "domme_onboarding_state",
        "bot_settings",
        "inactive_users",
        "bot_users_pii_cleared",
        "the_count",
    }

    queries = _queries(connection)
    assert "user_terms_acceptance" not in queries
    table_calls = [
        call
        for call in connection.execute_calls
        if "bot_settings" not in call[0]
    ]
    for query, params in table_calls:
        assert " AND guild_id = $2" in query
        assert params == (555, 4242)


def test_delete_in_guild_scopes_bot_settings_keys_to_that_guild():
    connection = _FakeConnection()
    repo = UserDataRepository(_FakeDatabase(connection))

    asyncio.run(repo.delete_user_in_guild(555, 4242))

    settings_call = next(
        call for call in connection.execute_calls if "bot_settings" in call[0]
    )
    _query, params = settings_call
    assert params == ("activity:4242:user:555:%", "inactivity:4242:user:555:%")


def test_delete_in_guild_returns_per_statement_counts():
    connection = _FakeConnection()
    connection.delete_counts = {"FROM sends": 7, "FROM subs": 2}
    repo = UserDataRepository(_FakeDatabase(connection))

    deleted = asyncio.run(repo.delete_user_in_guild(555, 4242))

    assert deleted["sends"] == 7
    assert deleted["subs"] == 2
    assert deleted["dommes"] == 1


# ---------------------------------------------------------------------------
# guilds_with_user_data
# ---------------------------------------------------------------------------


def test_guilds_with_user_data_returns_distinct_ints():
    connection = _FakeConnection(
        fetch_responses=[[{"guild_id": 10}, {"guild_id": 20}]]
    )
    repo = UserDataRepository(_FakeDatabase(connection))

    result = asyncio.run(repo.guilds_with_user_data(555))

    assert result == [10, 20]
    query = connection.fetch_calls[0][0]
    assert "DISTINCT guild_id" in query
    # Excludes terms (no guild) and never scans the block list.
    assert "user_terms_acceptance" not in query
    assert "bot_users" not in query


def test_guilds_with_user_data_empty():
    connection = _FakeConnection(fetch_responses=[[]])
    repo = UserDataRepository(_FakeDatabase(connection))
    assert asyncio.run(repo.guilds_with_user_data(555)) == []


# ---------------------------------------------------------------------------
# command-tag parsing + schema alignment
# ---------------------------------------------------------------------------


def test_rows_affected_parses_delete_tag():
    assert _rows_affected("DELETE 0") == 0
    assert _rows_affected("DELETE 12") == 12
    assert _rows_affected("") == 0
    assert _rows_affected("DELETE") == 0


def test_targeted_columns_exist_in_build_scripts():
    """Guard the DELETE column names against the canonical build scripts."""

    core = Path("db/build/001_core_schema.sql").read_text(encoding="utf-8")
    sub_names = Path("db/build/004_sub_send_names.sql").read_text(encoding="utf-8")
    count_recovery = Path("db/build/005_count_recovery.sql").read_text(encoding="utf-8")
    change_requests = Path("db/build/006_send_change_requests.sql").read_text(encoding="utf-8")
    dm_prefs = Path("db/build/008_dm_preferences.sql").read_text(encoding="utf-8")
    terms = Path("db/build/009_terms_acceptance.sql").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS dommes" in core
    assert "CREATE TABLE IF NOT EXISTS subs" in core
    assert "CREATE TABLE IF NOT EXISTS sends" in core
    assert "domme_user_id BIGINT NOT NULL" in core
    assert "sub_user_id BIGINT" in core
    # FK id columns the ordering-safe deletes rely on.
    assert "domme_id BIGINT REFERENCES dommes(id)" in core
    assert "sub_id BIGINT REFERENCES subs(id)" in core
    assert "required_domme_id BIGINT REFERENCES dommes(id)" in count_recovery
    assert "discord_user_id BIGINT NOT NULL" in sub_names
    assert "failed_user_id BIGINT NOT NULL" in count_recovery
    assert "required_domme_user_id BIGINT" in count_recovery
    assert "discord_user_id BIGINT NOT NULL" in count_recovery  # count_blocks
    assert "domme_user_id BIGINT NOT NULL" in change_requests
    assert "approved_by_user_id BIGINT" in change_requests
    assert "CREATE TABLE IF NOT EXISTS domme_onboarding_state" in dm_prefs
    assert "discord_user_id BIGINT NOT NULL" in dm_prefs
    assert "CREATE TABLE IF NOT EXISTS user_terms_acceptance" in terms
    assert "discord_user_id BIGINT PRIMARY KEY" in terms
