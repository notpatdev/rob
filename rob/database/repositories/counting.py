from __future__ import annotations

from datetime import datetime, timezone

from asyncpg import Record

from rob.database.connection import Database
from rob.models import CountBlock, CountRecoveryWindow, CountingState


def _build_counting_state(row: Record) -> CountingState:
    return CountingState(
        guild_id=int(row["guild_id"]),
        channel_id=row["channel_id"],
        current_number=int(row["current_number"]),
        last_user_id=row["last_user_id"],
        is_enabled=bool(row["is_enabled"]),
        pending_restore=bool(row["pending_restore"]),
        updated_at=row["updated_at"],
    )


def _build_recovery_window(row: Record) -> CountRecoveryWindow:
    return CountRecoveryWindow(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        channel_id=int(row["channel_id"]),
        failed_user_id=int(row["failed_user_id"]),
        failed_user_role=str(row["failed_user_role"]),
        required_domme_user_id=int(row["required_domme_user_id"])
        if row["required_domme_user_id"] is not None
        else None,
        required_domme_id=int(row["required_domme_id"]) if row["required_domme_id"] is not None else None,
        expected_number=int(row["expected_number"]),
        attempted_content=str(row["attempted_content"]) if row["attempted_content"] is not None else None,
        started_at=row["started_at"],
        expires_at=row["expires_at"],
        resolved_at=row["resolved_at"],
        resolution=str(row["resolution"]) if row["resolution"] is not None else None,
        created_at=row["created_at"],
    )


def _build_count_block(row: Record) -> CountBlock:
    return CountBlock(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        discord_user_id=int(row["discord_user_id"]),
        reason=str(row["reason"]),
        blocked_until=row["blocked_until"],
        created_at=row["created_at"],
    )


class CountingRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def get(self, guild_id: int) -> CountingState | None:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM the_count WHERE guild_id = $1",
                guild_id,
            )
        if row is None:
            return None
        return _build_counting_state(row)

    async def upsert(
        self,
        *,
        guild_id: int,
        channel_id: int | None,
        current_number: int,
        last_user_id: int | None,
        is_enabled: bool,
        pending_restore: bool,
    ) -> CountingState:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO the_count (
                    guild_id,
                    channel_id,
                    current_number,
                    last_user_id,
                    is_enabled,
                    pending_restore
                )
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (guild_id) DO UPDATE SET
                    channel_id = EXCLUDED.channel_id,
                    current_number = EXCLUDED.current_number,
                    last_user_id = EXCLUDED.last_user_id,
                    is_enabled = EXCLUDED.is_enabled,
                    pending_restore = EXCLUDED.pending_restore,
                    updated_at = now()
                RETURNING *
                """,
                guild_id,
                channel_id,
                current_number,
                last_user_id,
                is_enabled,
                pending_restore,
            )
        assert row is not None
        return _build_counting_state(row)

    async def create_recovery_window(
        self,
        *,
        guild_id: int,
        channel_id: int,
        failed_user_id: int,
        failed_user_role: str,
        required_domme_user_id: int | None,
        required_domme_id: int | None,
        expected_number: int,
        attempted_content: str | None,
        started_at: datetime,
        expires_at: datetime,
    ) -> CountRecoveryWindow:
        async with self.database.acquire() as connection:
            await connection.execute(
                """
                UPDATE count_recovery_windows
                SET resolved_at = now(),
                    resolution = 'cancelled'
                WHERE guild_id = $1
                  AND channel_id = $2
                  AND resolved_at IS NULL
                """,
                guild_id,
                channel_id,
            )
            row = await connection.fetchrow(
                """
                INSERT INTO count_recovery_windows (
                    guild_id,
                    channel_id,
                    failed_user_id,
                    failed_user_role,
                    required_domme_user_id,
                    required_domme_id,
                    expected_number,
                    attempted_content,
                    started_at,
                    expires_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                RETURNING *
                """,
                guild_id,
                channel_id,
                failed_user_id,
                failed_user_role,
                required_domme_user_id,
                required_domme_id,
                expected_number,
                attempted_content,
                started_at,
                expires_at,
            )
        assert row is not None
        return _build_recovery_window(row)

    async def get_active_recovery_window(self, guild_id: int, channel_id: int) -> CountRecoveryWindow | None:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT *
                FROM count_recovery_windows
                WHERE guild_id = $1
                  AND channel_id = $2
                  AND resolved_at IS NULL
                ORDER BY started_at DESC, id DESC
                LIMIT 1
                """,
                guild_id,
                channel_id,
            )
        if row is None:
            return None
        return _build_recovery_window(row)

    async def list_active_recovery_windows(self) -> list[CountRecoveryWindow]:
        async with self.database.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT *
                FROM count_recovery_windows
                WHERE resolved_at IS NULL
                ORDER BY expires_at ASC, id ASC
                """
            )
        return [_build_recovery_window(row) for row in rows]

    async def list_expired_active_recovery_windows(self, *, now: datetime | None = None) -> list[CountRecoveryWindow]:
        cutoff = now or datetime.now(timezone.utc)
        async with self.database.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT *
                FROM count_recovery_windows
                WHERE resolved_at IS NULL
                  AND expires_at <= $1
                ORDER BY expires_at ASC, id ASC
                """,
                cutoff,
            )
        return [_build_recovery_window(row) for row in rows]

    async def resolve_recovery_window(self, *, window_id: int, resolution: str) -> bool:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                UPDATE count_recovery_windows
                SET resolved_at = now(),
                    resolution = $2
                WHERE id = $1
                  AND resolved_at IS NULL
                RETURNING id
                """,
                window_id,
                resolution,
            )
        return row is not None

    async def get_active_block(self, guild_id: int, discord_user_id: int, *, now: datetime | None = None) -> CountBlock | None:
        cutoff = now or datetime.now(timezone.utc)
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT *
                FROM count_blocks
                WHERE guild_id = $1
                  AND discord_user_id = $2
                  AND blocked_until > $3
                LIMIT 1
                """,
                guild_id,
                discord_user_id,
                cutoff,
            )
        if row is None:
            return None
        return _build_count_block(row)

    async def upsert_block(
        self,
        *,
        guild_id: int,
        discord_user_id: int,
        reason: str,
        blocked_until: datetime,
    ) -> CountBlock:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO count_blocks (
                    guild_id,
                    discord_user_id,
                    reason,
                    blocked_until
                )
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (guild_id, discord_user_id) DO UPDATE SET
                    reason = EXCLUDED.reason,
                    blocked_until = EXCLUDED.blocked_until
                RETURNING *
                """,
                guild_id,
                discord_user_id,
                reason,
                blocked_until,
            )
        assert row is not None
        return _build_count_block(row)
