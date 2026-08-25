from __future__ import annotations

from datetime import datetime

from rob.database.connection import Database


class BlacklistRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def add(
        self,
        *,
        discord_user_id: int,
        reason: str | None,
        created_by: int | None,
        guild_id: int | None = None,
    ) -> None:
        tracked_guild_id = guild_id if guild_id is not None else 0
        async with self.database.acquire() as connection:
            await connection.execute(
                """
                INSERT INTO bot_users (
                    guild_id,
                    discord_user_id,
                    discord_username,
                    discord_display_name,
                    status,
                    first_seen_at,
                    last_seen_at,
                    created_at,
                    updated_at
                )
                VALUES ($1, $2, NULL, left($3, 250), 'blocked', now(), now(), now(), now())
                ON CONFLICT (guild_id, discord_user_id) DO UPDATE SET
                    status = 'blocked',
                    discord_display_name = COALESCE(EXCLUDED.discord_display_name, bot_users.discord_display_name),
                    updated_at = now()
                """,
                tracked_guild_id,
                discord_user_id,
                reason or (f"blocked by {created_by}" if created_by is not None else "manual"),
            )

    async def remove(self, discord_user_id: int) -> None:
        async with self.database.acquire() as connection:
            await connection.execute(
                """
                UPDATE bot_users
                SET status = 'allowed',
                    updated_at = now()
                WHERE discord_user_id = $1
                """,
                discord_user_id,
            )

    async def contains(self, discord_user_id: int) -> bool:
        async with self.database.acquire() as connection:
            value = await connection.fetchval(
                """
                SELECT 1
                FROM bot_users
                WHERE discord_user_id = $1
                  AND status = 'blocked'
                LIMIT 1
                """,
                discord_user_id,
            )
        return value is not None

    async def list_entries(self, *, limit: int = 200) -> list[tuple[int, str | None, int | None, datetime]]:
        async with self.database.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT discord_user_id, discord_display_name AS reason, NULL::bigint AS created_by, updated_at AS created_at
                FROM bot_users
                WHERE status = 'blocked'
                ORDER BY updated_at DESC
                LIMIT $1
                """,
                max(1, limit),
            )
        return [
            (
                int(row["discord_user_id"]),
                row["reason"],
                int(row["created_by"]) if row["created_by"] is not None else None,
                row["created_at"],
            )
            for row in rows
        ]
