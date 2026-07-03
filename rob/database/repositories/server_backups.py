"""Hourly server-backup snapshots and major-change approvals.

asyncpg returns JSONB columns as text (no custom codec registered), so this
module serialises with json.dumps on write and parses with json.loads on read.
"""

from __future__ import annotations

import json
from typing import Any

from asyncpg import Record

from rob.database.connection import Database
from rob.database.repositories.models import ServerBackup, ServerBackupApproval


def _loads(raw: Any, default: Any) -> Any:
    if raw is None:
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _build_backup(row: Record) -> ServerBackup:
    return ServerBackup(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        snapshot=_loads(row["snapshot"], {}),
        is_baseline=bool(row["is_baseline"]),
        created_at=row["created_at"],
    )


def _build_approval(row: Record) -> ServerBackupApproval:
    return ServerBackupApproval(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        status=str(row["status"]),
        changes=_loads(row["changes"], []),
        change_signature=row["change_signature"],
        pending_snapshot=_loads(row["pending_snapshot"], {}),
        baseline_backup_id=row["baseline_backup_id"],
        required_approvals=int(row["required_approvals"]),
        approved_by=[int(value) for value in _loads(row["approved_by"], [])],
        channel_id=row["channel_id"],
        message_id=row["message_id"],
        decided_by_user_id=row["decided_by_user_id"],
        decision_reason=row["decision_reason"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        decided_at=row["decided_at"],
    )


class ServerBackupsRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    # -- backups --------------------------------------------------------------

    async def create_backup(
        self,
        *,
        guild_id: int,
        snapshot: dict,
        is_baseline: bool = True,
    ) -> ServerBackup:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO server_backups (guild_id, snapshot, is_baseline)
                VALUES ($1, $2::jsonb, $3)
                RETURNING *
                """,
                guild_id,
                json.dumps(snapshot),
                is_baseline,
            )
        return _build_backup(row)

    async def get_latest_backup(self, guild_id: int) -> ServerBackup | None:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT * FROM server_backups
                WHERE guild_id = $1
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                guild_id,
            )
        return _build_backup(row) if row is not None else None

    async def count_backups(self, guild_id: int) -> int:
        async with self.database.acquire() as connection:
            value = await connection.fetchval(
                "SELECT count(*) FROM server_backups WHERE guild_id = $1",
                guild_id,
            )
        return int(value or 0)

    # -- approvals ------------------------------------------------------------

    async def get_pending_approval(self, guild_id: int) -> ServerBackupApproval | None:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT * FROM server_backup_approvals
                WHERE guild_id = $1 AND status = 'pending'
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                guild_id,
            )
        return _build_approval(row) if row is not None else None

    async def get_approval(self, approval_id: int) -> ServerBackupApproval | None:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM server_backup_approvals WHERE id = $1",
                approval_id,
            )
        return _build_approval(row) if row is not None else None

    async def list_pending_approvals(self) -> list[ServerBackupApproval]:
        async with self.database.acquire() as connection:
            rows = await connection.fetch(
                "SELECT * FROM server_backup_approvals WHERE status = 'pending'"
            )
        return [_build_approval(row) for row in rows]

    async def get_last_decided(self, guild_id: int) -> ServerBackupApproval | None:
        """Most recently decided (approved/rejected) approval for a guild."""

        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT * FROM server_backup_approvals
                WHERE guild_id = $1 AND status IN ('approved', 'rejected', 'superseded')
                ORDER BY decided_at DESC NULLS LAST, id DESC
                LIMIT 1
                """,
                guild_id,
            )
        return _build_approval(row) if row is not None else None

    async def create_approval(
        self,
        *,
        guild_id: int,
        changes: list,
        change_signature: str | None,
        pending_snapshot: dict,
        baseline_backup_id: int | None,
        required_approvals: int,
        channel_id: int | None = None,
        message_id: int | None = None,
    ) -> ServerBackupApproval:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO server_backup_approvals (
                    guild_id,
                    status,
                    changes,
                    change_signature,
                    pending_snapshot,
                    baseline_backup_id,
                    required_approvals,
                    approved_by,
                    channel_id,
                    message_id
                )
                VALUES ($1, 'pending', $2::jsonb, $3, $4::jsonb, $5, $6, '[]'::jsonb, $7, $8)
                RETURNING *
                """,
                guild_id,
                json.dumps(changes),
                change_signature,
                json.dumps(pending_snapshot),
                baseline_backup_id,
                required_approvals,
                channel_id,
                message_id,
            )
        return _build_approval(row)

    async def set_delivery(
        self,
        *,
        approval_id: int,
        channel_id: int,
        message_id: int,
    ) -> ServerBackupApproval | None:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                UPDATE server_backup_approvals
                SET channel_id = $2, message_id = $3, updated_at = now()
                WHERE id = $1
                RETURNING *
                """,
                approval_id,
                channel_id,
                message_id,
            )
        return _build_approval(row) if row is not None else None

    async def set_baseline(
        self,
        *,
        approval_id: int,
        baseline_backup_id: int,
    ) -> ServerBackupApproval | None:
        """Record the baseline backup for an already-decided approval. The
        winner of the atomic finalize transition creates it and links it here.
        """

        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                UPDATE server_backup_approvals
                SET baseline_backup_id = $2, updated_at = now()
                WHERE id = $1
                RETURNING *
                """,
                approval_id,
                baseline_backup_id,
            )
        return _build_approval(row) if row is not None else None

    async def add_approver(
        self,
        *,
        approval_id: int,
        user_id: int,
    ) -> ServerBackupApproval | None:
        """Append a distinct approver. Returns the updated row (still pending)."""

        async with self.database.transaction() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM server_backup_approvals WHERE id = $1 FOR UPDATE",
                approval_id,
            )
            if row is None:
                return None
            approval = _build_approval(row)
            if approval.status != "pending":
                return approval
            if user_id in approval.approved_by:
                return approval
            approvers = [*approval.approved_by, user_id]
            updated = await connection.fetchrow(
                """
                UPDATE server_backup_approvals
                SET approved_by = $2::jsonb, updated_at = now()
                WHERE id = $1
                RETURNING *
                """,
                approval_id,
                json.dumps(approvers),
            )
        return _build_approval(updated) if updated is not None else None

    async def finalize(
        self,
        *,
        approval_id: int,
        status: str,
        decided_by_user_id: int | None,
        decision_reason: str | None = None,
        baseline_backup_id: int | None = None,
    ) -> ServerBackupApproval | None:
        if status not in {"approved", "rejected", "superseded"}:
            raise ValueError(f"Unsupported approval status: {status}")
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                UPDATE server_backup_approvals
                SET status = $2,
                    decided_by_user_id = $3,
                    decision_reason = COALESCE($4, decision_reason),
                    baseline_backup_id = COALESCE($5, baseline_backup_id),
                    decided_at = now(),
                    updated_at = now()
                WHERE id = $1 AND status = 'pending'
                RETURNING *
                """,
                approval_id,
                status,
                decided_by_user_id,
                decision_reason,
                baseline_backup_id,
            )
        return _build_approval(row) if row is not None else None
