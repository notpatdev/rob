from __future__ import annotations

from asyncpg import Record
from asyncpg.exceptions import UniqueViolationError

from rob.database.connection import Database
from rob.models import NewSend, QueueStatus, SendRecord
from rob.utils.send_ids import build_public_send_id, parse_public_send_id


def _build_send(row: Record) -> SendRecord:
    return SendRecord(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        domme_id=row["domme_id"],
        domme_user_id=int(row["domme_user_id"]),
        sub_id=row["sub_id"],
        sub_user_id=row["sub_user_id"],
        sub_name=row["sub_name"],
        amount_cents=int(row["amount_cents"]),
        currency=str(row["currency"]),
        method=row["method"],
        source=str(row["source"]),
        item_name=row["item_name"],
        item_image_url=row["item_image_url"],
        external_id=row["external_id"],
        event_id=row["event_id"],
        fallback_event_hash=row["fallback_event_hash"],
        is_private=bool(row["is_private"]),
        seeded=bool(row["seeded"]),
        sent_at=row["sent_at"],
        received_at=row["received_at"],
        discord_post_status=str(row["discord_post_status"]),
        discord_posted_at=row["discord_posted_at"],
        discord_message_id=row["discord_message_id"],
        discord_post_error=row["discord_post_error"],
        created_at=row["created_at"],
        is_test_send=bool(row["is_test_send"]) if "is_test_send" in row else False,
        _public_send_id=row["public_send_id"] if "public_send_id" in row else None,
        original_amount_cents=(
            int(row["original_amount_cents"])
            if "original_amount_cents" in row and row["original_amount_cents"] is not None
            else None
        ),
        original_currency=(
            str(row["original_currency"])
            if "original_currency" in row and row["original_currency"] is not None
            else None
        ),
    )


class SendsRepository:
    VALID_STATUSES = ("pending", "queued_maintenance", "posted", "failed", "ignored")

    def __init__(self, database: Database) -> None:
        self.database = database

    async def insert(self, send: NewSend) -> SendRecord | None:
        try:
            async with self.database.transaction() as connection:
                row = await connection.fetchrow(
                    """
                    INSERT INTO sends (
                        guild_id,
                        domme_id,
                        domme_user_id,
                        sub_id,
                        sub_user_id,
                        sub_name,
                        amount_cents,
                        currency,
                        original_amount_cents,
                        original_currency,
                        method,
                        source,
                        item_name,
                        item_image_url,
                        external_id,
                        event_id,
                        fallback_event_hash,
                        is_private,
                        seeded,
                        sent_at,
                        discord_post_status,
                        is_test_send
                    )
                    VALUES (
                        $1, $2, $3, $4, $5,
                        $6, $7, $8, $9, $10,
                        $11, $12, $13, $14, $15,
                        $16, $17, $18, $19, $20,
                        $21, $22
                    )
                    RETURNING *
                    """,
                    send.guild_id,
                    send.domme_id,
                    send.domme_user_id,
                    send.sub_id,
                    send.sub_user_id,
                    send.sub_name,
                    send.amount_cents,
                    send.currency,
                    send.original_amount_cents,
                    send.original_currency,
                    send.method,
                    send.source,
                    send.item_name,
                    send.item_image_url,
                    send.external_id,
                    send.event_id,
                    send.fallback_event_hash,
                    send.is_private,
                    send.seeded,
                    send.sent_at,
                    send.discord_post_status,
                    send.is_test_send,
                )
                assert row is not None
                return await self._ensure_public_send_id_on_connection(
                    connection,
                    _build_send(row),
                )
        except UniqueViolationError:
            return None

    async def get(self, send_id: int) -> SendRecord | None:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow("SELECT * FROM sends WHERE id = $1", send_id)
        if row is None:
            return None
        return _build_send(row)

    async def get_by_id(self, send_id: int) -> SendRecord | None:
        return await self.get(send_id)

    async def get_by_public_id(self, public_id: str) -> SendRecord | None:
        normalized = public_id.strip().upper()
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                "SELECT * FROM sends WHERE public_send_id = $1",
                normalized,
            )
        if row is not None:
            return _build_send(row)

        parsed = parse_public_send_id(public_id)
        if parsed is None:
            return None
        send = await self.get(parsed[0])
        if send is None:
            return None
        if send.public_send_id != normalized:
            return None
        return await self.ensure_public_send_id(send)

    async def list_sends_for_user(
        self,
        guild_id: int,
        user_id: int,
        *,
        limit: int = 5,
    ) -> list[SendRecord]:
        async with self.database.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT *
                FROM sends
                WHERE guild_id = $1
                AND discord_post_status = 'posted'
                AND (domme_user_id = $2 OR sub_user_id = $2)
                ORDER BY sent_at DESC, id DESC
                LIMIT $3
                """,
                guild_id,
                user_id,
                limit,
            )
        return [_build_send(row) for row in rows]

    async def list_sends_for_domme(
        self,
        guild_id: int,
        domme_user_id: int,
        *,
        limit: int = 5,
    ) -> list[SendRecord]:
        async with self.database.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT *
                FROM sends
                WHERE guild_id = $1
                AND discord_post_status = 'posted'
                AND domme_user_id = $2
                ORDER BY sent_at DESC, id DESC
                LIMIT $3
                """,
                guild_id,
                domme_user_id,
                limit,
            )
        return [_build_send(row) for row in rows]

    async def list_sends_for_sub(
        self,
        guild_id: int,
        sub_user_id: int,
        *,
        limit: int = 5,
    ) -> list[SendRecord]:
        async with self.database.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT *
                FROM sends
                WHERE guild_id = $1
                AND discord_post_status = 'posted'
                AND sub_user_id = $2
                ORDER BY sent_at DESC, id DESC
                LIMIT $3
                """,
                guild_id,
                sub_user_id,
                limit,
            )
        return [_build_send(row) for row in rows]

    async def fetch_for_status(self, status: str, *, limit: int = 50) -> list[SendRecord]:
        async with self.database.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT *
                FROM sends
                WHERE discord_post_status = $1
                ORDER BY received_at ASC, id ASC
                LIMIT $2
                """,
                status,
                limit,
            )
        return [_build_send(row) for row in rows]

    async def release_queued_maintenance(self) -> int:
        async with self.database.acquire() as connection:
            result = await connection.execute(
                """
                UPDATE sends
                SET discord_post_status = 'pending'
                WHERE discord_post_status = 'queued_maintenance'
                """
            )
        return int(result.rsplit(" ", 1)[-1])

    async def update_status(self, send_id: int, status: str) -> bool:
        """Update the discord_post_status of a send."""
        if status not in self.VALID_STATUSES:
            raise ValueError(f"Invalid status: {status}. Must be one of {self.VALID_STATUSES}")

        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                UPDATE sends
                SET discord_post_status = $1
                WHERE id = $2
                RETURNING id
                """,
                status,
                send_id,
            )
        return row is not None

    async def mark_posted(self, send_id: int, *, message_id: int | None) -> None:
        async with self.database.acquire() as connection:
            await connection.execute(
                """
                UPDATE sends
                SET
                    discord_post_status = 'posted',
                    discord_posted_at = now(),
                    discord_message_id = $2,
                    discord_post_error = NULL
                WHERE id = $1
                """,
                send_id,
                message_id,
            )

    async def mark_failed(self, send_id: int, *, error: str) -> None:
        async with self.database.acquire() as connection:
            await connection.execute(
                """
                UPDATE sends
                SET
                    discord_post_status = 'failed',
                    discord_post_error = left($2, 500)
                WHERE id = $1
                """,
                send_id,
                error,
            )

    async def mark_ignored(self, send_id: int, *, reason: str | None = None) -> int:
        async with self.database.acquire() as connection:
            result = await connection.execute(
                """
                UPDATE sends
                SET
                    discord_post_status = 'ignored',
                    discord_post_error = CASE
                        WHEN $2::text IS NULL OR $2::text = '' THEN discord_post_error
                        ELSE left($2, 500)
                    END
                WHERE id = $1
                  AND discord_post_status <> 'ignored'
                """,
                send_id,
                reason,
            )
        return int(result.rsplit(" ", 1)[-1])

    async def update_amount(
        self,
        send_id: int,
        *,
        amount_cents: int,
        currency: str = "USD",
    ) -> SendRecord | None:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                UPDATE sends
                SET
                    amount_cents = $2,
                    currency = $3
                WHERE id = $1
                RETURNING *
                """,
                send_id,
                amount_cents,
                (currency or "USD").upper(),
            )
        if row is None:
            return None
        return _build_send(row)

    async def count_statuses(self) -> QueueStatus:
        async with self.database.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT discord_post_status, COUNT(*) AS count
                FROM sends
                GROUP BY discord_post_status
                """
            )
        counts = {status: 0 for status in self.VALID_STATUSES}
        for row in rows:
            counts[str(row["discord_post_status"])] = int(row["count"])
        return QueueStatus(
            pending=counts["pending"],
            queued_maintenance=counts["queued_maintenance"],
            posted=counts["posted"],
            failed=counts["failed"],
            ignored=counts["ignored"],
        )

    async def count_for_guild(self, guild_id: int) -> int:
        async with self.database.acquire() as connection:
            value = await connection.fetchval(
                """
                SELECT COUNT(*)
                FROM sends
                WHERE guild_id = $1
                """,
                guild_id,
            )
        return int(value or 0)

    async def total_cents_for_guild(self, guild_id: int) -> int:
        async with self.database.acquire() as connection:
            value = await connection.fetchval(
                """
                SELECT COALESCE(SUM(amount_cents), 0)
                FROM sends
                WHERE guild_id = $1
                """,
                guild_id,
            )
        return int(value or 0)

    async def list_sends(
        self,
        *,
        guild_id: int | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[SendRecord]:
        clauses: list[str] = []
        params: list[object] = []
        if guild_id is not None:
            params.append(guild_id)
            clauses.append(f"guild_id = ${len(params)}")
        if status is not None and status != "all":
            params.append(status)
            clauses.append(f"discord_post_status = ${len(params)}")
        params.append(limit)
        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        async with self.database.acquire() as connection:
            rows = await connection.fetch(
                f"""
                SELECT *
                FROM sends
                {where_sql}
                ORDER BY received_at DESC, id DESC
                LIMIT ${len(params)}
                """,
                *params,
            )
        return [_build_send(row) for row in rows]

    async def force_mark_posted(self, send_id: int) -> int:
        async with self.database.acquire() as connection:
            result = await connection.execute(
                """
                UPDATE sends
                SET
                    discord_post_status = 'posted',
                    discord_posted_at = COALESCE(discord_posted_at, now()),
                    discord_post_error = NULL
                WHERE id = $1
                """,
                send_id,
            )
        return int(result.rsplit(" ", 1)[-1])

    async def repair_send_domme_user_ids(
        self,
        *,
        guild_id: int,
        dry_run: bool = True,
    ) -> tuple[int, int]:
        async with self.database.transaction() as connection:
            candidates = await connection.fetch(
                """
                SELECT
                    s.id,
                    s.domme_user_id AS old_domme_user_id,
                    d.discord_user_id AS new_domme_user_id
                FROM sends s
                JOIN dommes d
                    ON d.id = s.domme_id
                WHERE s.guild_id = $1
                AND d.guild_id = s.guild_id
                AND s.domme_user_id <> d.discord_user_id
                ORDER BY s.id ASC
                """,
                guild_id,
            )
            updated = 0
            if not dry_run:
                result = await connection.execute(
                    """
                    UPDATE sends s
                    SET domme_user_id = d.discord_user_id
                    FROM dommes d
                    WHERE s.guild_id = $1
                    AND d.id = s.domme_id
                    AND d.guild_id = s.guild_id
                    AND s.domme_user_id <> d.discord_user_id
                    """,
                    guild_id,
                )
                updated = int(result.rsplit(" ", 1)[-1])
        return len(candidates), updated

    async def repair_mention_sub_links(
        self,
        *,
        guild_id: int | None = None,
        dry_run: bool = True,
    ) -> tuple[int, int]:
        """Attribute legacy sends whose ``sub_name`` is a raw Discord mention.

        Older manual sends stored the raw ``<@123>`` mention token as the
        sending name with no ``sub_user_id`` (this happened when a Dom/me picked
        a member from the @-autocomplete in the free-text ``/add`` sub field), so
        they rendered as "@User with no nickname claimed" and were never linked
        to the user. This extracts the id, links the send, and — when the user is
        a registered sub — restores their canonical send name; otherwise the raw
        token is cleared so it no longer shows the claim hint.

        Pass ``guild_id`` to scope the repair to one guild, or ``None`` for all
        guilds. Returns ``(candidates, updated)`` where ``updated`` is ``0`` on a
        dry run.
        """

        mention_pattern = r"^<@!?[0-9]+>$"
        async with self.database.transaction() as connection:
            candidates = await connection.fetch(
                """
                SELECT id
                FROM sends
                WHERE sub_user_id IS NULL
                AND sub_name ~ $1
                AND ($2::bigint IS NULL OR guild_id = $2)
                ORDER BY id ASC
                """,
                mention_pattern,
                guild_id,
            )
            updated = 0
            if not dry_run and candidates:
                result = await connection.execute(
                    """
                    UPDATE sends s
                    SET
                        sub_user_id = repaired.uid,
                        sub_id = repaired.sub_id,
                        sub_name = repaired.send_name
                    FROM (
                        SELECT
                            c.id,
                            c.uid,
                            sub.id AS sub_id,
                            sub.send_name AS send_name
                        FROM (
                            SELECT
                                id,
                                guild_id,
                                substring(sub_name from '^<@!?([0-9]+)>$')::bigint AS uid
                            FROM sends
                            WHERE sub_user_id IS NULL
                            AND sub_name ~ $1
                            AND ($2::bigint IS NULL OR guild_id = $2)
                        ) c
                        LEFT JOIN subs sub
                            ON sub.guild_id = c.guild_id
                            AND sub.discord_user_id = c.uid
                    ) repaired
                    WHERE s.id = repaired.id
                    """,
                    mention_pattern,
                    guild_id,
                )
                updated = int(result.rsplit(" ", 1)[-1])
        return len(candidates), updated

    async def ensure_public_send_id(self, send: SendRecord) -> SendRecord:
        if send.stored_public_send_id:
            return send
        async with self.database.transaction() as connection:
            return await self._ensure_public_send_id_on_connection(connection, send)

    async def backfill_public_send_ids(self, *, limit: int | None = None) -> int:
        async with self.database.transaction() as connection:
            if limit is None:
                rows = await connection.fetch(
                    """
                    SELECT *
                    FROM sends
                    WHERE public_send_id IS NULL
                    ORDER BY id ASC
                    """
                )
            else:
                rows = await connection.fetch(
                    """
                    SELECT *
                    FROM sends
                    WHERE public_send_id IS NULL
                    ORDER BY id ASC
                    LIMIT $1
                    """,
                    limit,
                )

            count = 0
            for row in rows:
                await self._ensure_public_send_id_on_connection(connection, _build_send(row))
                count += 1
        return count

    async def mark_known_test_sends(self, *, test_gifter_usernames: list[str]) -> int:
        if not test_gifter_usernames:
            return 0
        async with self.database.acquire() as connection:
            result = await connection.execute(
                """
                UPDATE sends
                SET is_test_send = true
                WHERE lower(COALESCE(sub_name, '')) = ANY($1::text[])
                """,
                test_gifter_usernames,
            )
        return int(result.rsplit(" ", 1)[-1])

    async def _ensure_public_send_id_on_connection(
        self,
        connection,
        send: SendRecord,
    ) -> SendRecord:
        if send.stored_public_send_id:
            return send

        public_send_id = build_public_send_id(send)
        row = await connection.fetchrow(
            """
            UPDATE sends
            SET public_send_id = $2
            WHERE id = $1
            AND public_send_id IS NULL
            RETURNING *
            """,
            send.id,
            public_send_id,
        )
        if row is not None:
            return _build_send(row)

        current = await connection.fetchrow("SELECT * FROM sends WHERE id = $1", send.id)
        assert current is not None
        return _build_send(current)
