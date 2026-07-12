from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from rob.throne.webhooks import create_webhook_app
from ops.migration import import_sqlite_to_postgres


REPO_ROOT = Path(__file__).resolve().parents[2]


def _create_sample_sqlite(path: Path) -> None:
    with sqlite3.connect(path) as sqlite:
        sqlite.execute(
            """
            CREATE TABLE bot_config (
                id INTEGER PRIMARY KEY,
                guild_id INTEGER,
                key TEXT,
                value TEXT
            )
            """
        )
        sqlite.execute(
            """
            CREATE TABLE event_dommes (
                id INTEGER PRIMARY KEY,
                guild_id INTEGER,
                discord_user_id INTEGER,
                throne_url TEXT,
                throne_handle TEXT,
                throne_creator_id TEXT
            )
            """
        )
        sqlite.execute(
            """
            CREATE TABLE event_subs (
                id INTEGER PRIMARY KEY,
                guild_id INTEGER,
                discord_user_id INTEGER,
                send_name TEXT
            )
            """
        )
        sqlite.execute(
            """
            CREATE TABLE event_sends (
                id INTEGER PRIMARY KEY,
                guild_id INTEGER,
                domme_user_id INTEGER,
                sub_user_id INTEGER,
                sub_name TEXT,
                amount_usd REAL,
                currency TEXT,
                item_name TEXT,
                event_id TEXT,
                sent_at TEXT
            )
            """
        )
        sqlite.execute(
            """
            CREATE TABLE event_messages (
                id INTEGER PRIMARY KEY,
                guild_id INTEGER,
                message_key TEXT,
                channel_id INTEGER,
                message_id INTEGER
            )
            """
        )
        sqlite.execute(
            """
            CREATE TABLE event_state (
                id INTEGER PRIMARY KEY,
                key TEXT,
                value TEXT
            )
            """
        )
        sqlite.execute(
            """
            CREATE TABLE rob_blacklist (
                id INTEGER PRIMARY KEY,
                guild_id INTEGER,
                discord_user_id INTEGER
            )
            """
        )
        sqlite.execute(
            """
            CREATE TABLE send_requests (
                id INTEGER PRIMARY KEY,
                guild_id INTEGER,
                domme_user_id INTEGER,
                sub_user_id INTEGER,
                amount_usd REAL,
                status TEXT,
                method TEXT,
                created_at TEXT
            )
            """
        )
        sqlite.execute(
            """
            CREATE TABLE throne_creators (
                id INTEGER PRIMARY KEY,
                guild_id INTEGER,
                discord_user_id INTEGER,
                throne_handle TEXT,
                throne_creator_id TEXT
            )
            """
        )
        sqlite.execute(
            """
            CREATE TABLE throne_wishlist_items (
                id INTEGER PRIMARY KEY,
                guild_id INTEGER,
                item_name TEXT
            )
            """
        )

        sqlite.execute(
            "INSERT INTO event_dommes (id, guild_id, discord_user_id, throne_url, throne_handle, throne_creator_id) VALUES (1, 10, 1001, 'https://throne.com/dom', 'dom', 'creator_1')"
        )
        sqlite.execute(
            "INSERT INTO throne_creators (id, guild_id, discord_user_id, throne_handle, throne_creator_id) VALUES (1, 10, 1001, 'dom', 'creator_1')"
        )
        sqlite.execute(
            "INSERT INTO event_subs (id, guild_id, discord_user_id, send_name) VALUES (1, 10, 2002, 'gifter')"
        )
        sqlite.execute(
            "INSERT INTO event_sends (id, guild_id, domme_user_id, sub_user_id, sub_name, amount_usd, currency, item_name, event_id, sent_at) VALUES (1, 10, 1001, 2002, 'gifter', 12.34, 'USD', 'Gift', 'evt_1', '2026-01-01T00:00:00+00:00')"
        )
        sqlite.execute(
            "INSERT INTO event_messages (id, guild_id, message_key, channel_id, message_id) VALUES (1, 10, 'leaderboard', 111, 222)"
        )
        sqlite.execute(
            "INSERT INTO send_requests (id, guild_id, domme_user_id, sub_user_id, amount_usd, status, method, created_at) VALUES (9, 10, 1001, 2002, 5.00, 'accepted', 'cashapp', '2026-01-01T01:00:00+00:00')"
        )
        sqlite.execute(
            "INSERT INTO throne_wishlist_items (id, guild_id, item_name) VALUES (1, 10, 'Legacy Wish')"
        )
        sqlite.execute(
            "INSERT INTO bot_config (id, guild_id, key, value) VALUES (1, 10, 'count:10:current_number', '777')"
        )
        sqlite.execute(
            "INSERT INTO bot_config (id, guild_id, key, value) VALUES (2, 10, 'count:10:is_enabled', 'true')"
        )
        sqlite.execute(
            "INSERT INTO bot_config (id, guild_id, key, value) VALUES (3, 10, 'inactivity:10:2002:initial_notice_sent', 'true')"
        )
        sqlite.commit()


def test_db_build_scripts_exist_under_scripts_db_build():
    build_dir = REPO_ROOT / "db" / "build"
    assert (build_dir / "001_core_schema.sql").exists()
    assert (build_dir / "002_indexes.sql").exists()
    assert not (build_dir / "003_achievements.sql").exists()
    assert (build_dir / "004_sub_send_names.sql").exists()
    assert (build_dir / "005_count_recovery.sql").exists()
    assert (build_dir / "006_send_change_requests.sql").exists()
    assert (build_dir / "007_send_update_requests.sql").exists()
    assert (build_dir / "008_dm_preferences.sql").exists()
    assert (build_dir / "009_terms_acceptance.sql").exists()
    assert (build_dir / "003_runtime_grants_template.sql").exists()
    assert (build_dir / "README.md").exists()
    grants_dir = REPO_ROOT / "db" / "grants"
    assert (grants_dir / "dev_rob_bot.sql").exists()
    assert (grants_dir / "dev_rehearsal_prod_roles.sql").exists()
    assert (grants_dir / "prod_rob_bot.sql").exists()
    assert (grants_dir / "prod_rob_webhook.sql").exists()
    assert not (REPO_ROOT / "rob" / "database" / "migrations").exists()


def test_db_build_scripts_contain_required_schema_and_index_statements():
    core_schema = (
        REPO_ROOT / "db" / "build" / "001_core_schema.sql"
    ).read_text(encoding="utf-8")
    indexes = (
        REPO_ROOT / "db" / "build" / "002_indexes.sql"
    ).read_text(encoding="utf-8")
    sub_send_names_schema = (
        REPO_ROOT / "db" / "build" / "004_sub_send_names.sql"
    ).read_text(encoding="utf-8")
    count_recovery_schema = (
        REPO_ROOT / "db" / "build" / "005_count_recovery.sql"
    ).read_text(encoding="utf-8")
    send_change_requests_schema = (
        REPO_ROOT / "db" / "build" / "006_send_change_requests.sql"
    ).read_text(encoding="utf-8")
    send_update_requests_schema = (
        REPO_ROOT / "db" / "build" / "007_send_update_requests.sql"
    ).read_text(encoding="utf-8")
    dm_preferences_schema = (
        REPO_ROOT / "db" / "build" / "008_dm_preferences.sql"
    ).read_text(encoding="utf-8")
    terms_acceptance_schema = (
        REPO_ROOT / "db" / "build" / "009_terms_acceptance.sql"
    ).read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS db_build_version" in core_schema
    assert "CREATE TABLE IF NOT EXISTS bot_users" in core_schema
    assert "CREATE TABLE IF NOT EXISTS sends" in core_schema
    assert "idx_sends_event_id_unique" in indexes
    assert "CREATE UNIQUE INDEX IF NOT EXISTS idx_sends_event_id_unique\nON sends (event_id);" in indexes
    assert "CREATE UNIQUE INDEX IF NOT EXISTS idx_sends_public_send_id_unique\nON sends (public_send_id);" in indexes
    assert "WHERE event_id IS NOT NULL" not in indexes
    assert "WHERE public_send_id IS NOT NULL" not in indexes
    assert "VALUES ('002_indexes'," in indexes
    assert "CREATE TABLE IF NOT EXISTS sub_send_names" in sub_send_names_schema
    assert "VALUES ('004_sub_send_names'," in sub_send_names_schema
    assert "CREATE TABLE IF NOT EXISTS count_recovery_windows" in count_recovery_schema
    assert "CREATE TABLE IF NOT EXISTS count_blocks" in count_recovery_schema
    assert "VALUES ('005_count_recovery'," in count_recovery_schema
    assert "CREATE TABLE IF NOT EXISTS send_change_requests" in send_change_requests_schema
    assert "'006_send_change_requests'" in send_change_requests_schema
    assert "send_update" in send_update_requests_schema
    assert "'007_send_update_requests'" in send_update_requests_schema
    # DM preferences migration drops achievements and adds new domme preference columns.
    assert "DROP TABLE IF EXISTS user_achievements" in dm_preferences_schema
    assert "DROP TABLE IF EXISTS achievement_events" in dm_preferences_schema
    assert "send_notifications_enabled" in dm_preferences_schema
    assert "leaderboard_visible" in dm_preferences_schema
    assert "notifications_snoozed_until" in dm_preferences_schema
    assert "preferences_deferred_until" in dm_preferences_schema
    assert "preferences_confirmed_at" in dm_preferences_schema
    assert "CREATE TABLE IF NOT EXISTS domme_onboarding_state" in dm_preferences_schema
    assert "'008_dm_preferences'" in dm_preferences_schema
    assert "CREATE TABLE IF NOT EXISTS user_terms_acceptance" in terms_acceptance_schema
    assert "terms_version" in terms_acceptance_schema
    assert "accepted_at" in terms_acceptance_schema
    assert "'009_terms_acceptance'" in terms_acceptance_schema


def test_deploy_scripts_do_not_run_schema_builder():
    bot_deploy = (REPO_ROOT / "deploy" / "scripts" / "deploy-bot-dev.sh").read_text(
        encoding="utf-8"
    )
    webhook_deploy = (
        REPO_ROOT / "deploy" / "scripts" / "deploy-webhook-dev.sh"
    ).read_text(encoding="utf-8")
    assert "run_migrations.py" not in bot_deploy
    assert "run_migrations.py" not in webhook_deploy


def test_removed_commands_and_portal_artifacts_are_absent():
    assert not (REPO_ROOT / "portal").exists()
    assert not (REPO_ROOT / "rob" / "discord" / "cogs" / "privacy.py").exists()
    assert not (REPO_ROOT / "rob" / "discord" / "cogs" / "broadcast.py").exists()
    sends_cog = (REPO_ROOT / "rob" / "discord" / "cogs" / "sends.py").read_text(
        encoding="utf-8"
    )
    assert "sendrequest" not in sends_cog.lower()


def test_runtime_grants_template_does_not_grant_schema_create_to_runtime_users():
    grants = (
        REPO_ROOT / "db" / "build" / "003_runtime_grants_template.sql"
    ).read_text(encoding="utf-8")
    assert "GRANT CREATE ON SCHEMA public TO prod_rob_bot" not in grants
    assert "GRANT CREATE ON SCHEMA public TO prod_rob_webhook" not in grants


def test_prod_webhook_grants_are_runtime_only_and_not_schema_changing():
    grants = (
        REPO_ROOT / "db" / "grants" / "prod_rob_webhook.sql"
    ).read_text(encoding="utf-8")
    for forbidden in ("GRANT CREATE", "GRANT ALTER", "GRANT DROP", "GRANT TRUNCATE"):
        assert forbidden not in grants
    assert "user_achievements" not in grants
    assert "achievement_events" not in grants
    assert "sub_send_names" in grants


def test_rehearsal_webhook_grants_do_not_reference_retired_achievement_tables():
    grants = (
        REPO_ROOT / "db" / "grants" / "dev_rehearsal_prod_roles.sql"
    ).read_text(encoding="utf-8")
    assert "user_achievements" not in grants
    assert "achievement_events" not in grants


def test_webhook_supports_new_and_compatibility_routes():
    app = create_webhook_app(
        settings=SimpleNamespace(
            throne_webhook_host="127.0.0.1",
            throne_webhook_port=8080,
            throne_webhook_require_signature=False,
            throne_public_key_pem=None,
            throne_webhook_timestamp_header="X-Signature-Timestamp",
            throne_webhook_signature_header="X-Signature-Ed25519",
            throne_webhook_signed_message_format="timestamp_dot_body",
            throne_webhook_max_timestamp_skew_seconds=300,
            throne_webhook_debug_log_payload=False,
            throne_parse_test_sends_as_real_sends=False,
            throne_test_gifter_usernames=("marie_123",),
        ),
        database=SimpleNamespace(),
    )
    route_paths = {
        route.resource.canonical
        for resource in app.router.resources()
        for route in resource
    }
    assert "/webhook/{creator_id}/{secret}" in route_paths
    assert "/throne/webhook/{creator_id}/{secret}" in route_paths


def test_importer_maps_dommes_subs_sends_count_and_inactivity(tmp_path: Path):
    sqlite_path = tmp_path / "legacy.sqlite3"
    _create_sample_sqlite(sqlite_path)

    payload = import_sqlite_to_postgres._build_payload(
        sqlite_path=sqlite_path,
        default_guild_id=10,
        include_wishlist_cache=False,
    )

    assert len(payload["dommes"]) == 1
    assert payload["dommes"][0]["throne_handle"] == "dom"
    assert len(payload["subs"]) == 1
    assert len(payload["sends"]) >= 1
    assert payload["sends"][0].amount_cents == 1234
    assert payload["the_count"][0]["current_number"] == 777
    assert payload["inactive_users"][0]["initial_notice_sent"] is True
    assert payload["report_counts"]["send_requests_total"] == 1
    assert payload["report_counts"]["send_requests_inserted"] == 1
    assert payload["report_counts"]["wishlist_rows_ignored"] == 1


def test_importer_reports_creator_merge_conflicts_and_missing_users(tmp_path: Path):
    sqlite_path = tmp_path / "legacy.sqlite3"
    _create_sample_sqlite(sqlite_path)
    with sqlite3.connect(sqlite_path) as sqlite:
        sqlite.execute(
            "INSERT INTO throne_creators (id, guild_id, discord_user_id, throne_handle, throne_creator_id) VALUES (2, 10, 1001, 'dom-alt', 'creator_2')"
        )
        sqlite.execute(
            "INSERT INTO throne_creators (id, guild_id, discord_user_id, throne_handle, throne_creator_id) VALUES (3, 10, NULL, 'missing', 'creator_missing')"
        )
        sqlite.commit()

    payload = import_sqlite_to_postgres._build_payload(
        sqlite_path=sqlite_path,
        default_guild_id=10,
        include_wishlist_cache=False,
    )

    assert payload["report_counts"]["throne_creator_conflicts"] >= 1
    assert payload["report_counts"]["throne_creators_missing_discord_user_id"] == 1
    assert payload["warnings"]


def test_importer_matches_throne_creators_by_handle_before_creating_duplicate_domme(
    tmp_path: Path,
):
    sqlite_path = tmp_path / "legacy.sqlite3"
    _create_sample_sqlite(sqlite_path)
    with sqlite3.connect(sqlite_path) as sqlite:
        sqlite.execute("DELETE FROM throne_creators")
        sqlite.execute(
            "INSERT INTO throne_creators (id, guild_id, discord_user_id, throne_handle, throne_creator_id) VALUES (1, 10, 9999, 'dom', 'creator_1')"
        )
        sqlite.commit()

    payload = import_sqlite_to_postgres._build_payload(
        sqlite_path=sqlite_path,
        default_guild_id=10,
        include_wishlist_cache=False,
    )

    assert len(payload["dommes"]) == 1
    assert payload["report_counts"]["throne_creators_matched_by_handle"] == 1


def test_importer_can_exclude_legacy_dommes_by_handle(tmp_path: Path):
    sqlite_path = tmp_path / "legacy.sqlite3"
    _create_sample_sqlite(sqlite_path)
    with sqlite3.connect(sqlite_path) as sqlite:
        sqlite.execute(
            "INSERT INTO throne_creators (id, guild_id, discord_user_id, throne_handle, throne_creator_id) VALUES (2, 10, 2002, 'remove-me', 'creator_remove')"
        )
        sqlite.commit()

    payload = import_sqlite_to_postgres._build_payload(
        sqlite_path=sqlite_path,
        default_guild_id=10,
        include_wishlist_cache=False,
        exclude_domme_handles={"remove-me"},
    )

    handles = {row.get("throne_handle") for row in payload["dommes"]}
    assert "remove-me" not in handles
    assert payload["report_counts"]["excluded_throne_creators_by_handle"] == 1


def test_importer_dry_run_does_not_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    sqlite_path = tmp_path / "legacy.sqlite3"
    _create_sample_sqlite(sqlite_path)
    called = {"write": 0}

    async def _fake_write_payload_to_postgres(**_kwargs):
        called["write"] += 1
        return {}

    monkeypatch.setattr(
        import_sqlite_to_postgres,
        "_write_payload_to_postgres",
        _fake_write_payload_to_postgres,
    )

    args = SimpleNamespace(
        sqlite=str(sqlite_path),
        database_url="postgresql://dev_rob_bot:pass@localhost:5432/rob_dev_v2",
        default_guild_id=10,
        dry_run=True,
        inspect_only=False,
        truncate_target=False,
        confirm_truncate=False,
        allow_prod_truncate=False,
        include_wishlist_cache=False,
        report_json=str(tmp_path / "dry-run-report.json"),
        exclude_domme_handle=[],
    )
    rc = asyncio.run(import_sqlite_to_postgres.main_async(args))

    assert rc == 0
    assert called["write"] == 0


def test_importer_can_infer_default_guild_id_from_source_sqlite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    sqlite_path = tmp_path / "legacy.sqlite3"
    _create_sample_sqlite(sqlite_path)
    called = {"write": 0}

    async def _fake_write_payload_to_postgres(**_kwargs):
        called["write"] += 1
        return {}

    monkeypatch.setattr(
        import_sqlite_to_postgres,
        "_write_payload_to_postgres",
        _fake_write_payload_to_postgres,
    )

    args = SimpleNamespace(
        sqlite=str(sqlite_path),
        database_url="postgresql://prod_rob_bot:pass@localhost:5432/rob_prod",
        default_guild_id=None,
        dry_run=True,
        inspect_only=False,
        truncate_target=False,
        confirm_truncate=False,
        allow_prod_truncate=False,
        include_wishlist_cache=False,
        report_json=str(tmp_path / "dry-run-report.json"),
        exclude_domme_handle=[],
    )

    rc = asyncio.run(import_sqlite_to_postgres.main_async(args))

    assert rc == 0
    assert called["write"] == 0
    report = (tmp_path / "dry-run-report.json").read_text(encoding="utf-8")
    assert '"resolved_default_guild_id": 10' in report


def test_importer_refuses_unsafe_prod_truncate(tmp_path: Path):
    sqlite_path = tmp_path / "legacy.sqlite3"
    _create_sample_sqlite(sqlite_path)

    args = SimpleNamespace(
        sqlite=str(sqlite_path),
        database_url="postgresql://prod_rob_bot:pass@localhost:5432/rob_prod",
        default_guild_id=10,
        dry_run=False,
        inspect_only=False,
        truncate_target=True,
        confirm_truncate=True,
        allow_prod_truncate=False,
        include_wishlist_cache=False,
        report_json="",
        exclude_domme_handle=[],
    )
    with pytest.raises(RuntimeError, match="Refusing to truncate a prod-like database"):
        asyncio.run(import_sqlite_to_postgres.main_async(args))
