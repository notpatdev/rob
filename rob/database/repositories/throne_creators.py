from __future__ import annotations

from asyncpg import Record

from rob.database.connection import Database
from rob.database.repositories.models import ThroneCreator


def _build_compat_creator(row: Record) -> ThroneCreator:
    return ThroneCreator(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        domme_id=int(row["id"]),
        discord_user_id=int(row["discord_user_id"]),
        throne_handle=str(row["throne_handle"] or ""),
        throne_creator_id=str(row["throne_creator_id"] or ""),
        hide_own_purchases=row["hide_own_purchases"],
        tracking_mode="webhook" if str(row["tracking_status"]) == "active" else "disabled",
        webhook_secret=row["webhook_secret"],
        webhook_secret_hash=row["webhook_secret_hash"],
        webhook_connected_at=row["webhook_connected_at"],
        overlay_detected=bool(row["overlay_detected"]),
        last_overlay_check_at=row["last_overlay_check_at"],
        last_successful_event_at=row["last_successful_event_at"],
        last_test_webhook_at=None,
        setup_verified_at=row["last_successful_event_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class ThroneCreatorsRepository:
    """Compatibility wrapper for legacy call sites; v2 keeps these fields on dommes."""

    def __init__(self, database: Database) -> None:
        self.database = database

    async def upsert_for_user(
        self,
        *,
        guild_id: int,
        domme_id: int | None,
        discord_user_id: int,
        throne_handle: str,
        throne_creator_id: str,
        hide_own_purchases: bool | None,
        tracking_mode: str,
        webhook_secret: str | None,
        webhook_secret_hash: str | None,
    ) -> ThroneCreator:
        del domme_id
        tracking_status = "active" if tracking_mode == "webhook" else "disabled"
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO dommes (
                    guild_id,
                    discord_user_id,
                    throne_handle,
                    throne_creator_id,
                    hide_own_purchases,
                    tracking_status,
                    profile_status,
                    webhook_secret,
                    webhook_secret_hash,
                    throne_url,
                    registered_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, 'active', $7, $8, NULL, now())
                ON CONFLICT (guild_id, discord_user_id) DO UPDATE SET
                    throne_handle = EXCLUDED.throne_handle,
                    throne_creator_id = EXCLUDED.throne_creator_id,
                    hide_own_purchases = EXCLUDED.hide_own_purchases,
                    tracking_status = EXCLUDED.tracking_status,
                    webhook_secret = COALESCE(dommes.webhook_secret, EXCLUDED.webhook_secret),
                    webhook_secret_hash = COALESCE(dommes.webhook_secret_hash, EXCLUDED.webhook_secret_hash),
                    updated_at = now()
                RETURNING *
                """,
                guild_id,
                discord_user_id,
                throne_handle,
                throne_creator_id,
                hide_own_purchases,
                tracking_status,
                webhook_secret,
                webhook_secret_hash,
            )
        assert row is not None
        return _build_compat_creator(row)

    async def get(self, creator_row_id: int) -> ThroneCreator | None:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow("SELECT * FROM dommes WHERE id = $1 LIMIT 1", creator_row_id)
        if row is None:
            return None
        return _build_compat_creator(row)

    async def get_by_user_id(self, guild_id: int, discord_user_id: int) -> ThroneCreator | None:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT *
                FROM dommes
                WHERE guild_id = $1
                  AND discord_user_id = $2
                LIMIT 1
                """,
                guild_id,
                discord_user_id,
            )
        if row is None:
            return None
        return _build_compat_creator(row)

    async def get_by_handle(self, guild_id: int, throne_handle: str) -> ThroneCreator | None:
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
        return _build_compat_creator(row)

    async def get_by_creator_id(self, throne_creator_id: str) -> list[ThroneCreator]:
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
        return [_build_compat_creator(row) for row in rows]

    async def list_for_guild(self, guild_id: int) -> list[ThroneCreator]:
        async with self.database.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT *
                FROM dommes
                WHERE guild_id = $1
                ORDER BY lower(COALESCE(throne_handle, '')) ASC, id ASC
                """,
                guild_id,
            )
        return [_build_compat_creator(row) for row in rows]

    async def remove_by_user_id(self, guild_id: int, discord_user_id: int) -> ThroneCreator | None:
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
        return _build_compat_creator(row)

    async def touch_successful_event(self, creator_row_id: int) -> None:
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
                creator_row_id,
            )

    async def mark_setup_verified(self, creator_row_id: int) -> None:
        await self.touch_successful_event(creator_row_id)
