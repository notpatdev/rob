from __future__ import annotations

import asyncio
import os
from pathlib import Path

from rob.config.settings import configure_logging, load_base_settings
from rob.database.connection import Database

DB_BUILD_DIR = Path(__file__).resolve().parents[2] / "db" / "build"
REQUIRED_DB_BUILD_VERSIONS = (
    "001_core_schema",
    "002_indexes",
    "004_sub_send_names",
    "005_count_recovery",
    "006_send_change_requests",
    "007_send_update_requests",
    "008_dm_preferences",
    "010_leaderboard_access_role",
    "011_send_fallback_hash_unique",
    "012_inactivity_backup_settings",
    "013_server_backups",
)

REQUIRED_TABLE_COLUMNS: dict[str, set[str]] = {
    "db_build_version": {"version", "applied_at", "notes"},
    "bot_settings": {"key", "value", "updated_at", "updated_by"},
    "bot_users": {
        "id",
        "guild_id",
        "discord_user_id",
        "discord_username",
        "discord_display_name",
        "status",
        "first_seen_at",
        "last_seen_at",
        "created_at",
        "updated_at",
    },
    "dommes": {
        "id",
        "guild_id",
        "bot_user_id",
        "discord_user_id",
        "throne_url",
        "throne_handle",
        "throne_creator_id",
        "tracking_status",
        "profile_status",
        "hide_own_purchases",
        "webhook_secret",
        "webhook_secret_hash",
        "webhook_connected_at",
        "overlay_detected",
        "last_overlay_check_at",
        "last_successful_event_at",
        "public_display_name",
        "public_display_name_updated_at",
        "registered_at",
        "created_at",
        "updated_at",
        "leaderboard_visible",
        "preferences_deferred_until",
        "preferences_confirmed_at",
    },
    "subs": {
        "id",
        "guild_id",
        "bot_user_id",
        "discord_user_id",
        "send_name",
        "profile_status",
        "registered_at",
        "created_at",
        "updated_at",
    },
    "sub_send_names": {
        "id",
        "guild_id",
        "sub_id",
        "discord_user_id",
        "send_name",
        "is_primary",
        "created_at",
        "updated_at",
    },
    "sends": {
        "id",
        "guild_id",
        "domme_id",
        "domme_user_id",
        "sub_id",
        "sub_user_id",
        "sub_name",
        "amount_cents",
        "currency",
        "method",
        "source",
        "item_name",
        "item_image_url",
        "logged_by",
        "external_id",
        "event_id",
        "fallback_event_hash",
        "public_send_id",
        "is_private",
        "is_test_send",
        "seeded",
        "sent_at",
        "received_at",
        "discord_post_status",
        "discord_posted_at",
        "discord_message_id",
        "discord_post_error",
        "created_at",
    },
    "vib_settings": {
        "guild_id",
        "registration_channel_id",
        "leaderboard_channel_id",
        "send_track_channel_id",
        "counting_channel_id",
        "report_channel_id",
        "warn_log_channel_id",
        "domme_role_id",
        "sub_role_id",
        "mod_role_id",
        "inactive_role_id",
        "leaderboard_view_role_id",
        "active_role_id",
        "unverified_role_id",
        "trial_mod_role_id",
        "backup_approval_channel_id",
        "carlbot_user_id",
        "created_at",
        "updated_at",
    },
    "vib_leaderboard": {
        "id",
        "guild_id",
        "leaderboard_key",
        "leaderboard_type",
        "title",
        "channel_id",
        "message_id",
        "public_token",
        "public_enabled",
        "public_theme",
        "last_refreshed_at",
        "created_at",
        "updated_at",
    },
    "the_count": {
        "guild_id",
        "channel_id",
        "current_number",
        "last_user_id",
        "is_enabled",
        "pending_restore",
        "updated_at",
    },
    "inactive_users": {
        "id",
        "guild_id",
        "bot_user_id",
        "discord_user_id",
        "inactive_role_assigned_at",
        "remove_at",
        "initial_notice_sent",
        "final_notice_sent",
        "status",
        "created_at",
        "updated_at",
    },
    "count_recovery_windows": {
        "id",
        "guild_id",
        "channel_id",
        "failed_user_id",
        "failed_user_role",
        "required_domme_user_id",
        "required_domme_id",
        "expected_number",
        "attempted_content",
        "started_at",
        "expires_at",
        "resolved_at",
        "resolution",
        "created_at",
    },
    "count_blocks": {
        "id",
        "guild_id",
        "discord_user_id",
        "reason",
        "blocked_until",
        "created_at",
    },
    "send_change_requests": {
        "id",
        "guild_id",
        "domme_user_id",
        "action",
        "status",
        "requested_by",
        "requested_sub_name",
        "amount_cents",
        "currency",
        "method",
        "note",
        "target_send_id",
        "decision_reason",
        "request_channel_id",
        "request_message_id",
        "approved_by_user_id",
        "approved_send_id",
        "created_at",
        "updated_at",
        "decided_at",
    },
    "domme_onboarding_state": {
        "id",
        "guild_id",
        "discord_user_id",
        "stage",
        "pending_throne_input",
        "pending_throne_handle",
        "pending_throne_creator_id",
        "dm_channel_id",
        "dm_message_id",
        "last_interaction_at",
        "completed_at",
        "created_at",
        "updated_at",
    },
    "server_backups": {
        "id",
        "guild_id",
        "snapshot",
        "is_baseline",
        "created_at",
    },
    "server_backup_approvals": {
        "id",
        "guild_id",
        "status",
        "changes",
        "change_signature",
        "pending_snapshot",
        "baseline_backup_id",
        "required_approvals",
        "approved_by",
        "channel_id",
        "message_id",
        "decided_by_user_id",
        "decision_reason",
        "created_at",
        "updated_at",
        "decided_at",
    },
}

WEBHOOK_REQUIRED_TABLES = {
    "db_build_version",
    "bot_settings",
    "bot_users",
    "dommes",
    "subs",
    "sub_send_names",
    "sends",
    "vib_settings",
    "vib_leaderboard",
}

BOT_TABLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "db_build_version": ("SELECT",),
    "bot_settings": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "bot_users": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "dommes": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "subs": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "sub_send_names": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "sends": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "vib_settings": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "vib_leaderboard": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "the_count": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "inactive_users": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "count_recovery_windows": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "count_blocks": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "send_change_requests": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "domme_onboarding_state": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "server_backups": ("SELECT", "INSERT", "UPDATE", "DELETE"),
    "server_backup_approvals": ("SELECT", "INSERT", "UPDATE", "DELETE"),
}

WEBHOOK_TABLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "db_build_version": ("SELECT",),
    "bot_settings": ("SELECT", "UPDATE"),
    "bot_users": ("SELECT", "INSERT", "UPDATE"),
    "dommes": ("SELECT", "UPDATE"),
    "subs": ("SELECT",),
    "sub_send_names": ("SELECT",),
    "sends": ("SELECT", "INSERT", "UPDATE"),
    "vib_settings": ("SELECT",),
    "vib_leaderboard": ("SELECT",),
}

BOT_RUNTIME_SEQUENCES = (
    "public.bot_users_id_seq",
    "public.dommes_id_seq",
    "public.subs_id_seq",
    "public.sub_send_names_id_seq",
    "public.sends_id_seq",
    "public.vib_leaderboard_id_seq",
    "public.inactive_users_id_seq",
    "public.count_recovery_windows_id_seq",
    "public.count_blocks_id_seq",
    "public.send_change_requests_id_seq",
    "public.domme_onboarding_state_id_seq",
    "public.server_backups_id_seq",
    "public.server_backup_approvals_id_seq",
)

WEBHOOK_RUNTIME_SEQUENCES = (
    "public.bot_users_id_seq",
    "public.sends_id_seq",
)


def _runtime_profile(current_user: str) -> str:
    override = os.getenv("ROB_CHECK_DB_PROFILE", "").strip().lower()
    if override:
        if override in {"bot", "webhook", "generic"}:
            return override
        raise RuntimeError(
            "Invalid ROB_CHECK_DB_PROFILE value. Expected one of: bot, webhook, generic."
        )

    if current_user.endswith("_webhook"):
        return "webhook"
    if current_user.endswith("_bot"):
        return "bot"
    return "generic"


def _required_tables_for_profile(profile: str) -> dict[str, set[str]]:
    if profile == "webhook":
        return {
            table_name: REQUIRED_TABLE_COLUMNS[table_name]
            for table_name in WEBHOOK_REQUIRED_TABLES
        }
    return REQUIRED_TABLE_COLUMNS


async def _table_exists(connection, table: str) -> bool:
    value = await connection.fetchval(
        "SELECT to_regclass($1) IS NOT NULL",
        f"public.{table}",
    )
    return bool(value)


async def _assert_table_columns(connection, table: str, required_columns: set[str]) -> None:
    rows = await connection.fetch(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
        AND table_name = $1
        """,
        table,
    )
    if not rows:
        raise RuntimeError(f"Missing required table: {table}")
    present = {str(row["column_name"]) for row in rows}
    missing = sorted(required_columns - present)
    if missing:
        raise RuntimeError(
            f"Table {table} is missing required columns: {', '.join(missing)}"
        )


async def _assert_no_schema_create_privilege(connection, *, current_user: str) -> None:
    has_create = await connection.fetchval(
        "SELECT has_schema_privilege(current_user, 'public', 'CREATE')",
    )
    if has_create:
        raise RuntimeError(
            f"Runtime user '{current_user}' has CREATE on schema public; "
            "runtime users must not have schema-changing privileges."
        )


async def _assert_runtime_permissions(
    connection,
    *,
    current_user: str,
    current_database: str,
) -> None:
    if not await connection.fetchval(
        "SELECT has_database_privilege(current_user, current_database(), 'CONNECT')",
    ):
        raise RuntimeError(
            f"Runtime user '{current_user}' cannot CONNECT to database '{current_database}'."
        )

    profile = _runtime_profile(current_user)
    if profile == "generic":
        return

    await _assert_no_schema_create_privilege(connection, current_user=current_user)

    if profile == "bot":
        required = BOT_TABLE_PERMISSIONS
        sequence_names = BOT_RUNTIME_SEQUENCES
    else:
        required = WEBHOOK_TABLE_PERMISSIONS
        sequence_names = WEBHOOK_RUNTIME_SEQUENCES

    missing: list[str] = []
    for table_name, privileges in required.items():
        for privilege in privileges:
            has_privilege = await connection.fetchval(
                "SELECT has_table_privilege(current_user, $1, $2)",
                f"public.{table_name}",
                privilege,
            )
            if not has_privilege:
                missing.append(f"{table_name}:{privilege}")

    for sequence_name in sequence_names:
        exists = await connection.fetchval("SELECT to_regclass($1) IS NOT NULL", sequence_name)
        if not exists:
            missing.append(f"{sequence_name}:missing")
            continue
        for privilege in ("USAGE", "SELECT", "UPDATE"):
            has_privilege = await connection.fetchval(
                "SELECT has_sequence_privilege(current_user, $1, $2)",
                sequence_name,
                privilege,
            )
            if not has_privilege:
                missing.append(f"{sequence_name}:{privilege}")

    if profile == "webhook":
        for table_name in ("sends", "bot_users"):
            has_delete = await connection.fetchval(
                "SELECT has_table_privilege(current_user, $1, 'DELETE')",
                f"public.{table_name}",
            )
            if has_delete:
                missing.append(f"{table_name}:DELETE_not_allowed")

    if missing:
        raise RuntimeError(
            "Runtime permission check failed for user "
            f"'{current_user}'. Missing or invalid privileges: {', '.join(missing)}"
        )


async def main() -> None:
    settings = load_base_settings()
    configure_logging(settings.log_level)

    database = Database(settings.database_url)
    await database.connect()

    try:
        if not await database.health_check():
            raise RuntimeError("Database check failed.")

        async with database.acquire() as connection:
            current_user = str(await connection.fetchval("SELECT current_user"))
            current_database = str(await connection.fetchval("SELECT current_database()"))
            profile = _runtime_profile(current_user)

            if not await _table_exists(connection, "db_build_version"):
                raise RuntimeError("Missing required table: db_build_version")

            rows = await connection.fetch("SELECT version FROM db_build_version")
            applied = {str(row["version"]) for row in rows}
            required_scripts = {
                version: DB_BUILD_DIR / f"{version}.sql"
                for version in REQUIRED_DB_BUILD_VERSIONS
            }
            missing_script_files = [
                version
                for version, script_path in required_scripts.items()
                if not script_path.exists()
            ]
            if missing_script_files:
                raise RuntimeError(
                    "Required DB build script file is missing: "
                    + ", ".join(missing_script_files)
                )

            missing_versions = sorted(set(REQUIRED_DB_BUILD_VERSIONS) - applied)
            if missing_versions:
                if "008_dm_preferences" in missing_versions:
                    raise RuntimeError(
                        "DM notification preference schema is missing.\n"
                        "Run db/build/008_dm_preferences.sql manually as "
                        "doadmin, then run the relevant grants file."
                    )
                if "010_leaderboard_access_role" in missing_versions:
                    raise RuntimeError(
                        "Leaderboard access role column is missing.\n"
                        "Run db/build/010_leaderboard_access_role.sql manually "
                        "as doadmin (no new grants required)."
                    )
                if "011_send_fallback_hash_unique" in missing_versions:
                    raise RuntimeError(
                        "Send de-duplication index is missing (webhook retries could "
                        "double-count sends).\n"
                        "Run db/build/011_send_fallback_hash_unique.sql manually "
                        "as doadmin (no new grants required)."
                    )
                if len(missing_versions) == 1:
                    raise RuntimeError(
                        f"Database is missing required DB build version: {missing_versions[0]}"
                    )
                raise RuntimeError(
                    "Database is missing required DB build versions: "
                    + ", ".join(missing_versions)
                )

            for table_name, required_columns in _required_tables_for_profile(profile).items():
                await _assert_table_columns(connection, table_name, required_columns)

            await _assert_runtime_permissions(
                connection,
                current_user=current_user,
                current_database=current_database,
            )

        print("Database check passed.")
    finally:
        await database.close()


if __name__ == "__main__":
    asyncio.run(main())
