from __future__ import annotations

from asyncpg import Record

from rob.database.connection import Database
from rob.models import SendChangeRequest


def _build_send_change_request(row: Record) -> SendChangeRequest:
    return SendChangeRequest(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        domme_user_id=int(row["domme_user_id"]),
        action=str(row["action"]),
        status=str(row["status"]),
        requested_by=str(row["requested_by"]),
        requested_sub_name=row["requested_sub_name"],
        amount_cents=int(row["amount_cents"]) if row["amount_cents"] is not None else None,
        currency=str(row["currency"]) if row["currency"] is not None else None,
        method=row["method"],
        note=row["note"],
        target_send_id=int(row["target_send_id"]) if row["target_send_id"] is not None else None,
        decision_reason=row["decision_reason"],
        request_channel_id=int(row["request_channel_id"]) if row["request_channel_id"] is not None else None,
        request_message_id=int(row["request_message_id"]) if row["request_message_id"] is not None else None,
        approved_by_user_id=(
            int(row["approved_by_user_id"]) if row["approved_by_user_id"] is not None else None
        ),
        approved_send_id=int(row["approved_send_id"]) if row["approved_send_id"] is not None else None,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        decided_at=row["decided_at"],
    )


class SendChangeRequestsRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def create_send_add_request(
        self,
        *,
        guild_id: int,
        domme_user_id: int,
        requested_by: str,
        amount_cents: int,
        currency: str,
        method: str,
        note: str | None,
        sub_name: str | None,
    ) -> SendChangeRequest:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO send_change_requests (
                    guild_id,
                    domme_user_id,
                    action,
                    status,
                    requested_by,
                    requested_sub_name,
                    amount_cents,
                    currency,
                    method,
                    note
                )
                VALUES ($1, $2, 'send_add', 'pending', $3, $4, $5, $6, $7, $8)
                RETURNING *
                """,
                guild_id,
                domme_user_id,
                requested_by,
                sub_name,
                amount_cents,
                currency,
                method,
                note,
            )
        assert row is not None
        return _build_send_change_request(row)

    async def create_send_remove_request(
        self,
        *,
        guild_id: int,
        domme_user_id: int,
        requested_by: str,
        target_send_id: int,
    ) -> SendChangeRequest:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO send_change_requests (
                    guild_id,
                    domme_user_id,
                    action,
                    status,
                    requested_by,
                    target_send_id
                )
                VALUES ($1, $2, 'send_remove', 'pending', $3, $4)
                RETURNING *
                """,
                guild_id,
                domme_user_id,
                requested_by,
                target_send_id,
            )
        assert row is not None
        return _build_send_change_request(row)

    async def create_send_update_request(
        self,
        *,
        guild_id: int,
        domme_user_id: int,
        requested_by: str,
        target_send_id: int,
        amount_cents: int,
        currency: str,
        note: str | None,
    ) -> SendChangeRequest:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO send_change_requests (
                    guild_id,
                    domme_user_id,
                    action,
                    status,
                    requested_by,
                    target_send_id,
                    amount_cents,
                    currency,
                    note
                )
                VALUES ($1, $2, 'send_update', 'pending', $3, $4, $5, $6, $7)
                RETURNING *
                """,
                guild_id,
                domme_user_id,
                requested_by,
                target_send_id,
                amount_cents,
                (currency or "USD").upper(),
                note,
            )
        assert row is not None
        return _build_send_change_request(row)

    async def get(self, request_id: int) -> SendChangeRequest | None:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM send_change_requests WHERE id = $1",
                request_id,
            )
        if row is None:
            return None
        return _build_send_change_request(row)

    async def list_pending(self) -> list[SendChangeRequest]:
        async with self.database.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT *
                FROM send_change_requests
                WHERE status = 'pending'
                ORDER BY created_at ASC, id ASC
                """
            )
        return [_build_send_change_request(row) for row in rows]

    async def set_delivery(
        self,
        *,
        request_id: int,
        request_channel_id: int,
        request_message_id: int,
    ) -> SendChangeRequest:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                UPDATE send_change_requests
                SET
                    request_channel_id = $2,
                    request_message_id = $3,
                    updated_at = now()
                WHERE id = $1
                RETURNING *
                """,
                request_id,
                request_channel_id,
                request_message_id,
            )
        if row is None:
            raise RuntimeError(f"Send change request not found: {request_id}")
        return _build_send_change_request(row)

    async def mark_rejected(
        self,
        *,
        request_id: int,
        approved_by_user_id: int,
        decision_reason: str | None = None,
    ) -> SendChangeRequest | None:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                UPDATE send_change_requests
                SET
                    status = 'rejected',
                    approved_by_user_id = $2,
                    decision_reason = $3,
                    decided_at = now(),
                    updated_at = now()
                WHERE id = $1
                  AND status = 'pending'
                RETURNING *
                """,
                request_id,
                approved_by_user_id,
                decision_reason,
            )
        if row is None:
            return None
        return _build_send_change_request(row)

    async def mark_approved(
        self,
        *,
        request_id: int,
        approved_by_user_id: int,
        approved_send_id: int | None = None,
        decision_reason: str | None = None,
    ) -> SendChangeRequest | None:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                UPDATE send_change_requests
                SET
                    status = 'approved',
                    approved_by_user_id = $2,
                    approved_send_id = $3,
                    decision_reason = $4,
                    decided_at = now(),
                    updated_at = now()
                WHERE id = $1
                  AND status = 'pending'
                RETURNING *
                """,
                request_id,
                approved_by_user_id,
                approved_send_id,
                decision_reason,
            )
        if row is None:
            return None
        return _build_send_change_request(row)

    async def mark_failed(
        self,
        *,
        request_id: int,
        approved_by_user_id: int | None,
        decision_reason: str,
    ) -> SendChangeRequest | None:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                UPDATE send_change_requests
                SET
                    status = 'failed',
                    approved_by_user_id = $2,
                    decision_reason = left($3, 500),
                    decided_at = now(),
                    updated_at = now()
                WHERE id = $1
                  AND status = 'pending'
                RETURNING *
                """,
                request_id,
                approved_by_user_id,
                decision_reason,
            )
        if row is None:
            return None
        return _build_send_change_request(row)
