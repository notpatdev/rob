from __future__ import annotations

from datetime import datetime, timezone

from asyncpg import Record

from rob.database.connection import Database
from rob.models import Sub, SubSendName


def _build_sub(row: Record) -> Sub:
    return Sub(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        discord_user_id=int(row["discord_user_id"]),
        send_name=str(row["send_name"]),
        registered_at=row["registered_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _build_sub_send_name(row: Record) -> SubSendName:
    return SubSendName(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        sub_id=int(row["sub_id"]),
        discord_user_id=int(row["discord_user_id"]),
        send_name=str(row["send_name"]),
        is_primary=bool(row["is_primary"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class SubsRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def upsert(
        self,
        *,
        guild_id: int,
        discord_user_id: int,
        send_name: str,
    ) -> Sub:
        return await self.upsert_with_send_names(
            guild_id=guild_id,
            discord_user_id=discord_user_id,
            send_names=[send_name],
        )

    async def upsert_with_send_names(
        self,
        *,
        guild_id: int,
        discord_user_id: int,
        send_names: list[str],
    ) -> Sub:
        if not send_names:
            raise ValueError("At least one send name is required.")
        if len(send_names) > 3:
            raise ValueError("A maximum of 3 send names is supported.")

        primary_name = send_names[0]
        registered_at = datetime.now(timezone.utc)
        async with self.database.transaction() as connection:
            conflicts = await connection.fetch(
                """
                SELECT send_name, discord_user_id
                FROM sub_send_names
                WHERE guild_id = $1
                  AND lower(send_name) = ANY($2::text[])
                  AND discord_user_id <> $3
                """,
                guild_id,
                [name.lower() for name in send_names],
                discord_user_id,
            )
            if conflicts:
                conflict_name = str(conflicts[0]["send_name"])
                raise ValueError(f"That sending name is already claimed: {conflict_name}")

            legacy_conflict = await connection.fetchrow(
                """
                SELECT send_name
                FROM subs
                WHERE guild_id = $1
                  AND lower(send_name) = ANY($2::text[])
                  AND discord_user_id <> $3
                LIMIT 1
                """,
                guild_id,
                [name.lower() for name in send_names],
                discord_user_id,
            )
            if legacy_conflict is not None:
                raise ValueError(f"That sending name is already claimed: {legacy_conflict['send_name']}")

            row = await connection.fetchrow(
                """
                INSERT INTO subs (
                    guild_id,
                    discord_user_id,
                    send_name,
                    registered_at
                )
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (guild_id, discord_user_id) DO UPDATE SET
                    send_name = EXCLUDED.send_name,
                    updated_at = now()
                RETURNING *
                """,
                guild_id,
                discord_user_id,
                primary_name,
                registered_at,
            )
            assert row is not None
            sub = _build_sub(row)
            await connection.execute(
                "DELETE FROM sub_send_names WHERE guild_id = $1 AND sub_id = $2",
                guild_id,
                sub.id,
            )
            for index, name in enumerate(send_names):
                await connection.execute(
                    """
                    INSERT INTO sub_send_names (
                        guild_id,
                        sub_id,
                        discord_user_id,
                        send_name,
                        is_primary
                    )
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    guild_id,
                    sub.id,
                    sub.discord_user_id,
                    name,
                    index == 0,
                )
            await connection.execute(
                """
                UPDATE sends
                SET
                    sub_id = $1,
                    sub_user_id = $2
                WHERE guild_id = $3
                AND sub_user_id IS NULL
                AND sub_name IS NOT NULL
                AND lower(sub_name) = ANY($4::text[])
                """,
                sub.id,
                sub.discord_user_id,
                guild_id,
                [name.lower() for name in send_names],
            )
        return sub

    async def get_by_user_id(self, guild_id: int, discord_user_id: int) -> Sub | None:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT *
                FROM subs
                WHERE guild_id = $1
                AND discord_user_id = $2
                """,
                guild_id,
                discord_user_id,
            )
        if row is None:
            return None
        return _build_sub(row)

    async def get_by_send_name(self, guild_id: int, send_name: str) -> Sub | None:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT *
                FROM subs
                WHERE guild_id = $1
                AND (
                    lower(send_name) = lower($2)
                    OR EXISTS (
                        SELECT 1
                        FROM sub_send_names
                        WHERE guild_id = subs.guild_id
                        AND sub_id = subs.id
                        AND lower(sub_send_names.send_name) = lower($2)
                    )
                )
                LIMIT 1
                """,
                guild_id,
                send_name,
            )
        if row is None:
            return None
        return _build_sub(row)

    async def get_by_name(self, guild_id: int, send_name: str) -> Sub | None:
        return await self.get_by_send_name(guild_id, send_name)

    async def list_send_names_for_user(self, guild_id: int, discord_user_id: int) -> list[SubSendName]:
        async with self.database.acquire() as connection:
            sub_row = await connection.fetchrow(
                """
                SELECT *
                FROM subs
                WHERE guild_id = $1
                AND discord_user_id = $2
                """,
                guild_id,
                discord_user_id,
            )
            if sub_row is None:
                return []
            sub = _build_sub(sub_row)
            rows = await connection.fetch(
                """
                SELECT *
                FROM sub_send_names
                WHERE guild_id = $1
                AND sub_id = $2
                ORDER BY is_primary DESC, lower(send_name) ASC, id ASC
                """,
                guild_id,
                sub.id,
            )
        if rows:
            return [_build_sub_send_name(row) for row in rows]
        return [
            SubSendName(
                id=0,
                guild_id=guild_id,
                sub_id=sub.id,
                discord_user_id=sub.discord_user_id,
                send_name=sub.send_name,
                is_primary=True,
                created_at=sub.created_at,
                updated_at=sub.updated_at,
            )
        ]

    async def list_for_guild(self, guild_id: int) -> list[Sub]:
        async with self.database.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT *
                FROM subs
                WHERE guild_id = $1
                ORDER BY registered_at ASC, lower(send_name) ASC
                """,
                guild_id,
            )
        return [_build_sub(row) for row in rows]

    async def remove_by_user_id(self, guild_id: int, discord_user_id: int) -> Sub | None:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                DELETE FROM subs
                WHERE guild_id = $1
                  AND discord_user_id = $2
                RETURNING *
                """,
                guild_id,
                discord_user_id,
            )
        if row is None:
            return None
        return _build_sub(row)

    async def remove_by_send_name(self, guild_id: int, send_name: str) -> Sub | None:
        sub = await self.get_by_send_name(guild_id, send_name)
        if sub is None:
            return None
        return await self.remove_by_user_id(guild_id, sub.discord_user_id)

    async def count(self, guild_id: int) -> int:
        async with self.database.acquire() as connection:
            value = await connection.fetchval(
                "SELECT COUNT(*) FROM subs WHERE guild_id = $1",
                guild_id,
            )
        return int(value or 0)
