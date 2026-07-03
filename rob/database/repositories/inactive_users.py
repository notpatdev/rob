"""Per-guild inactive-member watchlist: when the Inactive role was assigned,
the kick deadline (remove_at), and notice-DM flags. The activity signal itself
lives in bot_settings under activity:{guild}:user:{uid}:last_active."""

from __future__ import annotations

from datetime import datetime

from asyncpg import Record

from rob.database.connection import Database
from rob.database.repositories.models import InactiveUser


def _build(row: Record) -> InactiveUser:
    return InactiveUser(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        bot_user_id=row["bot_user_id"],
        discord_user_id=int(row["discord_user_id"]),
        inactive_role_assigned_at=row["inactive_role_assigned_at"],
        remove_at=row["remove_at"],
        initial_notice_sent=bool(row["initial_notice_sent"]),
        final_notice_sent=bool(row["final_notice_sent"]),
        status=str(row["status"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class InactiveUsersRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def get(self, guild_id: int, discord_user_id: int) -> InactiveUser | None:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT * FROM inactive_users
                WHERE guild_id = $1 AND discord_user_id = $2
                """,
                guild_id,
                discord_user_id,
            )
        return _build(row) if row is not None else None

    async def list_for_guild(
        self,
        guild_id: int,
        *,
        statuses: tuple[str, ...] | None = None,
    ) -> list[InactiveUser]:
        query = "SELECT * FROM inactive_users WHERE guild_id = $1"
        params: list[object] = [guild_id]
        if statuses:
            query += " AND status = ANY($2::text[])"
            params.append(list(statuses))
        query += " ORDER BY remove_at ASC NULLS LAST, discord_user_id ASC"
        async with self.database.acquire() as connection:
            rows = await connection.fetch(query, *params)
        return [_build(row) for row in rows]

    async def start_watching(
        self,
        *,
        guild_id: int,
        discord_user_id: int,
        inactive_role_assigned_at: datetime,
        remove_at: datetime,
        bot_user_id: int | None = None,
    ) -> InactiveUser:
        """Open (or reset) a watch row when a member is freshly marked inactive."""

        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO inactive_users (
                    guild_id,
                    bot_user_id,
                    discord_user_id,
                    inactive_role_assigned_at,
                    remove_at,
                    initial_notice_sent,
                    final_notice_sent,
                    status,
                    created_at,
                    updated_at
                )
                VALUES ($1, $2, $3, $4, $5, false, false, 'watching', now(), now())
                ON CONFLICT (guild_id, discord_user_id) DO UPDATE SET
                    bot_user_id = COALESCE(EXCLUDED.bot_user_id, inactive_users.bot_user_id),
                    inactive_role_assigned_at = EXCLUDED.inactive_role_assigned_at,
                    remove_at = EXCLUDED.remove_at,
                    initial_notice_sent = false,
                    final_notice_sent = false,
                    status = 'watching',
                    updated_at = now()
                RETURNING *
                """,
                guild_id,
                bot_user_id,
                discord_user_id,
                inactive_role_assigned_at,
                remove_at,
            )
        return _build(row)

    async def mark_initial_notice(self, guild_id: int, discord_user_id: int) -> None:
        async with self.database.acquire() as connection:
            await connection.execute(
                """
                UPDATE inactive_users
                SET initial_notice_sent = true,
                    status = CASE WHEN status = 'watching' THEN 'notice_sent' ELSE status END,
                    updated_at = now()
                WHERE guild_id = $1 AND discord_user_id = $2
                """,
                guild_id,
                discord_user_id,
            )

    async def mark_final_notice(self, guild_id: int, discord_user_id: int) -> None:
        async with self.database.acquire() as connection:
            await connection.execute(
                """
                UPDATE inactive_users
                SET final_notice_sent = true,
                    status = 'final_notice_sent',
                    updated_at = now()
                WHERE guild_id = $1 AND discord_user_id = $2
                """,
                guild_id,
                discord_user_id,
            )

    async def clear(self, guild_id: int, discord_user_id: int) -> None:
        """Drop the watch row when a member becomes active again or leaves."""

        async with self.database.acquire() as connection:
            await connection.execute(
                "DELETE FROM inactive_users WHERE guild_id = $1 AND discord_user_id = $2",
                guild_id,
                discord_user_id,
            )
