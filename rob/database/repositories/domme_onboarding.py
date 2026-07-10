"""Repository for the DM-based Dom/me onboarding flow (test guild only).

Onboarding state is short-lived flow data — Throne input that has been entered
but not yet confirmed, the DM channel/message ids the bot is editing in place,
and the current stage of the wizard. Once the flow completes, the row is
marked ``completed`` (it can also be re-used to resume an interrupted flow).
"""

from __future__ import annotations

from asyncpg import Record

from rob.database.connection import Database
from rob.database.repositories.models import DommeOnboardingState

ALLOWED_STAGES: tuple[str, ...] = (
    "intro",
    "awaiting_throne_input",
    "awaiting_identity_confirm",
    "awaiting_webhook",
    "awaiting_preferences",
    "completed",
)

# Friendly aliases used by service / cog layers.
STAGE_INTRO = "intro"
STAGE_AWAITING_THRONE_INPUT = "awaiting_throne_input"
STAGE_CONFIRMING_IDENTITY = "awaiting_identity_confirm"
STAGE_AWAITING_WEBHOOK_TEST = "awaiting_webhook"
STAGE_AWAITING_PREFERENCES = "awaiting_preferences"
STAGE_COMPLETE = "completed"


def _build(row: Record) -> DommeOnboardingState:
    return DommeOnboardingState(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        discord_user_id=int(row["discord_user_id"]),
        stage=str(row["stage"]),
        pending_throne_input=row["pending_throne_input"],
        pending_throne_handle=row["pending_throne_handle"],
        pending_throne_creator_id=row["pending_throne_creator_id"],
        dm_channel_id=int(row["dm_channel_id"]) if row["dm_channel_id"] is not None else None,
        dm_message_id=int(row["dm_message_id"]) if row["dm_message_id"] is not None else None,
        last_interaction_at=row["last_interaction_at"],
        completed_at=row["completed_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class DommeOnboardingRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def start(
        self,
        *,
        guild_id: int,
        discord_user_id: int,
        dm_channel_id: int | None = None,
        dm_message_id: int | None = None,
    ) -> DommeOnboardingState:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO domme_onboarding_state (
                    guild_id,
                    discord_user_id,
                    stage,
                    dm_channel_id,
                    dm_message_id
                )
                VALUES ($1, $2, 'intro', $3, $4)
                ON CONFLICT (guild_id, discord_user_id) DO UPDATE SET
                    stage = 'intro',
                    dm_channel_id = COALESCE(EXCLUDED.dm_channel_id, domme_onboarding_state.dm_channel_id),
                    dm_message_id = COALESCE(EXCLUDED.dm_message_id, domme_onboarding_state.dm_message_id),
                    last_interaction_at = now(),
                    completed_at = NULL,
                    updated_at = now()
                RETURNING *
                """,
                guild_id,
                discord_user_id,
                dm_channel_id,
                dm_message_id,
            )
        assert row is not None
        return _build(row)

    async def get(self, *, guild_id: int, discord_user_id: int) -> DommeOnboardingState | None:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT * FROM domme_onboarding_state
                WHERE guild_id = $1 AND discord_user_id = $2
                """,
                guild_id,
                discord_user_id,
            )
        return _build(row) if row is not None else None

    async def get_latest_for_user(
        self, *, discord_user_id: int
    ) -> DommeOnboardingState | None:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT *
                FROM domme_onboarding_state
                WHERE discord_user_id = $1
                ORDER BY
                    CASE WHEN completed_at IS NULL THEN 0 ELSE 1 END,
                    last_interaction_at DESC,
                    updated_at DESC,
                    created_at DESC
                LIMIT 1
                """,
                discord_user_id,
            )
        return _build(row) if row is not None else None

    async def set_stage(
        self,
        *,
        guild_id: int,
        discord_user_id: int,
        stage: str,
    ) -> DommeOnboardingState | None:
        if stage not in ALLOWED_STAGES:
            raise ValueError(f"Unknown onboarding stage: {stage!r}")
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                UPDATE domme_onboarding_state
                SET stage = $3,
                    last_interaction_at = now(),
                    completed_at = CASE WHEN $3 = 'completed' THEN now() ELSE completed_at END,
                    updated_at = now()
                WHERE guild_id = $1 AND discord_user_id = $2
                RETURNING *
                """,
                guild_id,
                discord_user_id,
                stage,
            )
        return _build(row) if row is not None else None

    async def set_pending_throne(
        self,
        *,
        guild_id: int,
        discord_user_id: int,
        throne_input: str | None = None,
        throne_handle: str | None = None,
        throne_creator_id: str | None = None,
    ) -> DommeOnboardingState | None:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                UPDATE domme_onboarding_state
                SET pending_throne_input = COALESCE($3, pending_throne_input),
                    pending_throne_handle = COALESCE($4, pending_throne_handle),
                    pending_throne_creator_id = COALESCE($5, pending_throne_creator_id),
                    last_interaction_at = now(),
                    updated_at = now()
                WHERE guild_id = $1 AND discord_user_id = $2
                RETURNING *
                """,
                guild_id,
                discord_user_id,
                throne_input,
                throne_handle,
                throne_creator_id,
            )
        return _build(row) if row is not None else None

    async def set_dm_message(
        self,
        *,
        guild_id: int,
        discord_user_id: int,
        dm_channel_id: int,
        dm_message_id: int,
    ) -> DommeOnboardingState | None:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                UPDATE domme_onboarding_state
                SET dm_channel_id = $3,
                    dm_message_id = $4,
                    last_interaction_at = now(),
                    updated_at = now()
                WHERE guild_id = $1 AND discord_user_id = $2
                RETURNING *
                """,
                guild_id,
                discord_user_id,
                dm_channel_id,
                dm_message_id,
            )
        return _build(row) if row is not None else None

    async def complete(self, *, guild_id: int, discord_user_id: int) -> None:
        await self.set_stage(
            guild_id=guild_id,
            discord_user_id=discord_user_id,
            stage="completed",
        )

    async def clear(self, *, guild_id: int, discord_user_id: int) -> None:
        async with self.database.acquire() as connection:
            await connection.execute(
                """
                DELETE FROM domme_onboarding_state
                WHERE guild_id = $1 AND discord_user_id = $2
                """,
                guild_id,
                discord_user_id,
            )
