from __future__ import annotations

from datetime import datetime, timezone

from asyncpg import Record

from rob.database.connection import Database
from rob.models import Domme


def _build_domme(row: Record) -> Domme:
    def _get(key: str, default=None):
        try:
            value = row[key]
        except (KeyError, IndexError):
            return default
        return default if value is None else value

    return Domme(
        id=int(row["id"]),
        bot_user_id=int(row["bot_user_id"]) if row["bot_user_id"] is not None else None,
        guild_id=int(row["guild_id"]),
        discord_user_id=int(row["discord_user_id"]),
        throne_url=row["throne_url"],
        throne_handle=row["throne_handle"],
        throne_creator_id=row["throne_creator_id"],
        tracking_status=str(row["tracking_status"]),
        profile_status=str(row["profile_status"]),
        hide_own_purchases=row["hide_own_purchases"],
        webhook_secret=row["webhook_secret"],
        webhook_secret_hash=row["webhook_secret_hash"],
        webhook_connected_at=row["webhook_connected_at"],
        overlay_detected=bool(row["overlay_detected"]),
        last_overlay_check_at=row["last_overlay_check_at"],
        last_successful_event_at=row["last_successful_event_at"],
        public_display_name=row["public_display_name"],
        public_display_name_updated_at=row["public_display_name_updated_at"],
        registered_at=row["registered_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        leaderboard_visible=bool(_get("leaderboard_visible", True)),
        preferences_deferred_until=_get("preferences_deferred_until"),
        preferences_confirmed_at=_get("preferences_confirmed_at"),
    )


class DommesRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def upsert(
        self,
        *,
        guild_id: int,
        discord_user_id: int,
        throne_url: str | None,
        throne_handle: str | None = None,
        throne_creator_id: str | None = None,
        hide_own_purchases: bool | None = None,
        tracking_status: str = "active",
        profile_status: str = "active",
        webhook_secret: str | None = None,
        webhook_secret_hash: str | None = None,
    ) -> Domme:
        registered_at = datetime.now(timezone.utc)
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO dommes (
                    guild_id,
                    discord_user_id,
                    throne_url,
                    throne_handle,
                    throne_creator_id,
                    hide_own_purchases,
                    tracking_status,
                    profile_status,
                    webhook_secret,
                    webhook_secret_hash,
                    registered_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                ON CONFLICT (guild_id, discord_user_id) DO UPDATE SET
                    throne_url = EXCLUDED.throne_url,
                    throne_handle = EXCLUDED.throne_handle,
                    throne_creator_id = EXCLUDED.throne_creator_id,
                    hide_own_purchases = EXCLUDED.hide_own_purchases,
                    tracking_status = EXCLUDED.tracking_status,
                    profile_status = EXCLUDED.profile_status,
                    webhook_secret = COALESCE(dommes.webhook_secret, EXCLUDED.webhook_secret),
                    webhook_secret_hash = COALESCE(dommes.webhook_secret_hash, EXCLUDED.webhook_secret_hash),
                    updated_at = now()
                RETURNING *
                """,
                guild_id,
                discord_user_id,
                throne_url,
                throne_handle,
                throne_creator_id,
                hide_own_purchases,
                tracking_status,
                profile_status,
                webhook_secret,
                webhook_secret_hash,
                registered_at,
            )
        assert row is not None
        return _build_domme(row)

    async def get(self, domme_id: int) -> Domme | None:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow("SELECT * FROM dommes WHERE id = $1", domme_id)
        if row is None:
            return None
        return _build_domme(row)

    async def get_by_user_id(self, guild_id: int, discord_user_id: int) -> Domme | None:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT *
                FROM dommes
                WHERE guild_id = $1
                  AND discord_user_id = $2
                """,
                guild_id,
                discord_user_id,
            )
        if row is None:
            return None
        return _build_domme(row)

    async def get_by_handle(self, guild_id: int, throne_handle: str) -> Domme | None:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT *
                FROM dommes
                WHERE guild_id = $1
                  AND lower(COALESCE(throne_handle, '')) = lower($2)
                LIMIT 1
                """,
                guild_id,
                throne_handle,
            )
        if row is None:
            return None
        return _build_domme(row)

    async def get_by_creator_id(self, throne_creator_id: str) -> list[Domme]:
        async with self.database.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT *
                FROM dommes
                WHERE throne_creator_id = $1
                ORDER BY created_at ASC
                """,
                throne_creator_id,
            )
        return [_build_domme(row) for row in rows]

    async def list_for_guild(self, guild_id: int) -> list[Domme]:
        async with self.database.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT *
                FROM dommes
                WHERE guild_id = $1
                ORDER BY registered_at ASC, discord_user_id ASC
                """,
                guild_id,
            )
        return [_build_domme(row) for row in rows]

    async def remove_by_user_id(self, guild_id: int, discord_user_id: int) -> Domme | None:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                DELETE FROM dommes
                WHERE guild_id = $1
                  AND discord_user_id = $2
                RETURNING *
                """,
                guild_id,
                discord_user_id,
            )
        if row is None:
            return None
        return _build_domme(row)

    async def count(self, guild_id: int) -> int:
        async with self.database.acquire() as connection:
            value = await connection.fetchval(
                "SELECT COUNT(*) FROM dommes WHERE guild_id = $1",
                guild_id,
            )
        return int(value or 0)

    async def set_public_display_name(self, *, guild_id: int, discord_user_id: int, label: str) -> None:
        async with self.database.acquire() as connection:
            await connection.execute(
                """
                UPDATE dommes
                SET public_display_name = $3,
                    public_display_name_updated_at = now(),
                    updated_at = now()
                WHERE guild_id = $1
                  AND discord_user_id = $2
                """,
                guild_id,
                discord_user_id,
                label,
            )

    async def touch_successful_event(self, domme_id: int) -> None:
        async with self.database.acquire() as connection:
            await connection.execute(
                """
                UPDATE dommes
                SET tracking_status = 'active',
                    webhook_connected_at = COALESCE(webhook_connected_at, now()),
                    last_successful_event_at = now(),
                    updated_at = now()
                WHERE id = $1
                """,
                domme_id,
            )

    async def mark_setup_verified(self, domme_id: int) -> None:
        async with self.database.acquire() as connection:
            await connection.execute(
                """
                UPDATE dommes
                SET tracking_status = 'active',
                    webhook_connected_at = COALESCE(webhook_connected_at, now()),
                    last_successful_event_at = now(),
                    updated_at = now()
                WHERE id = $1
                """,
                domme_id,
            )

    async def rotate_webhook_secret(
        self,
        *,
        guild_id: int,
        discord_user_id: int,
        webhook_secret: str,
        webhook_secret_hash: str,
        throne_url: str | None = None,
        throne_handle: str | None = None,
        throne_creator_id: str | None = None,
        hide_own_purchases: bool | None = None,
    ) -> Domme:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                UPDATE dommes
                SET
                    throne_url = COALESCE($3, throne_url),
                    throne_handle = COALESCE($4, throne_handle),
                    throne_creator_id = COALESCE($5, throne_creator_id),
                    hide_own_purchases = COALESCE($6, hide_own_purchases),
                    webhook_secret = $7,
                    webhook_secret_hash = $8,
                    webhook_connected_at = NULL,
                    last_successful_event_at = NULL,
                    tracking_status = 'disabled',
                    updated_at = now()
                WHERE guild_id = $1
                  AND discord_user_id = $2
                RETURNING *
                """,
                guild_id,
                discord_user_id,
                throne_url,
                throne_handle,
                throne_creator_id,
                hide_own_purchases,
                webhook_secret,
                webhook_secret_hash,
            )
        if row is None:
            raise ValueError("That Dom/me is no longer registered.")
        return _build_domme(row)

    # --- Leaderboard + preference state --------------------------------------

    async def set_preferences(
        self,
        *,
        guild_id: int,
        discord_user_id: int,
        leaderboard_visible: bool | None = None,
        preferences_deferred_until: datetime | None = None,
        clear_defer: bool = False,
        confirm: bool = False,
    ) -> Domme | None:
        """Update leaderboard/preference state. None means "do not change";
        clear_defer nulls preferences_deferred_until, confirm=True stamps
        preferences_confirmed_at."""

        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                UPDATE dommes
                SET
                    leaderboard_visible        = COALESCE($3, leaderboard_visible),
                    preferences_deferred_until = CASE
                        WHEN $5::bool THEN NULL
                        WHEN $4::timestamptz IS NOT NULL THEN $4
                        ELSE preferences_deferred_until
                    END,
                    preferences_confirmed_at = CASE
                        WHEN $6::bool THEN now()
                        ELSE preferences_confirmed_at
                    END,
                    updated_at = now()
                WHERE guild_id = $1
                  AND discord_user_id = $2
                RETURNING *
                """,
                guild_id,
                discord_user_id,
                leaderboard_visible,
                preferences_deferred_until,
                clear_defer,
                confirm,
            )
        return _build_domme(row) if row is not None else None

    async def defer_preferences(
        self,
        *,
        guild_id: int,
        discord_user_id: int,
        until: datetime,
    ) -> Domme | None:
        return await self.set_preferences(
            guild_id=guild_id,
            discord_user_id=discord_user_id,
            preferences_deferred_until=until,
        )
