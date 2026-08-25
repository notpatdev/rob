from __future__ import annotations

from rob.database.connection import Database


class BotUsersRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def upsert_user(
        self,
        *,
        guild_id: int,
        discord_user_id: int,
        discord_username: str | None = None,
        discord_display_name: str | None = None,
        status: str = "allowed",
    ) -> None:
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
                VALUES ($1, $2, $3, $4, $5, now(), now(), now(), now())
                ON CONFLICT (guild_id, discord_user_id) DO UPDATE SET
                    discord_username = COALESCE(EXCLUDED.discord_username, bot_users.discord_username),
                    discord_display_name = COALESCE(EXCLUDED.discord_display_name, bot_users.discord_display_name),
                    status = EXCLUDED.status,
                    last_seen_at = now(),
                    updated_at = now()
                """,
                guild_id,
                discord_user_id,
                discord_username,
                discord_display_name,
                status,
            )

    async def set_status(self, *, discord_user_id: int, status: str) -> None:
        async with self.database.acquire() as connection:
            await connection.execute(
                """
                UPDATE bot_users
                SET status = $2,
                    updated_at = now()
                WHERE discord_user_id = $1
                """,
                discord_user_id,
                status,
            )

    async def is_blocked(self, *, discord_user_id: int) -> bool:
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
