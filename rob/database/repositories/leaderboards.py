from __future__ import annotations

from datetime import datetime

from asyncpg import Record

from rob.database.connection import Database
from rob.models import (
    LatestTrackedSend,
    LeaderboardEntry,
    LeaderboardDiagnostics,
    LeaderboardMessageRef,
    PersonalStatsSummary,
    LeaderboardSummary,
)


def _normalized_test_usernames(test_gifter_usernames: tuple[str, ...] | list[str]) -> list[str]:
    return [value.strip().lower() for value in test_gifter_usernames if value.strip()]


def _counted_send_filter(alias: str = "s") -> str:
    return f"""
        {alias}.guild_id = $1
        AND {alias}.discord_post_status = 'posted'
        AND {alias}.is_private = false
        AND (
            $2::bool
            OR NOT (
                COALESCE({alias}.is_test_send, false)
                OR lower(COALESCE({alias}.sub_name, '')) = ANY($3::text[])
            )
            OR ($4::bigint IS NOT NULL AND {alias}.domme_user_id = $4)
        )
    """


def _valid_sends_cte() -> str:
    return f"""
        WITH valid_sends AS (
            SELECT
                s.*,
                COALESCE(d_by_id.discord_user_id, s.domme_user_id) AS recipient_user_id
            FROM sends s
            LEFT JOIN dommes d_by_id
                ON d_by_id.id = s.domme_id
                AND d_by_id.guild_id = s.guild_id
            WHERE {_counted_send_filter("s")}
        )
    """


def _build_message_ref(row: Record) -> LeaderboardMessageRef:
    message_key = row["leaderboard_key"] if "leaderboard_key" in row else row["message_key"]
    return LeaderboardMessageRef(
        id=int(row["id"]),
        guild_id=int(row["guild_id"]),
        message_key=str(message_key),
        leaderboard_type=row["leaderboard_type"],
        channel_id=int(row["channel_id"]),
        message_id=int(row["message_id"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _build_latest_send(row: Record) -> LatestTrackedSend:
    return LatestTrackedSend(
        id=int(row["id"]),
        domme_user_id=int(row["domme_user_id"]),
        sub_user_id=int(row["sub_user_id"]) if row["sub_user_id"] is not None else None,
        sub_name=row["sub_name"],
        amount_cents=int(row["amount_cents"]),
        currency=str(row["currency"]),
        method=row["method"],
        source=str(row["source"]),
        item_name=row["item_name"],
        item_image_url=row["item_image_url"],
        sent_at=row["sent_at"],
    )


class LeaderboardsRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def get_summary(
        self,
        guild_id: int,
        *,
        include_test_sends: bool = False,
        test_gifter_usernames: tuple[str, ...] | list[str] = (),
        owner_test_user_id: int | None = None,
    ) -> LeaderboardSummary:
        usernames = _normalized_test_usernames(test_gifter_usernames)
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                f"""
                {_valid_sends_cte()}
                SELECT
                    COALESCE((SELECT SUM(amount_cents) FROM valid_sends), 0) AS total_cents,
                    (SELECT COUNT(*) FROM valid_sends) AS send_count,
                    (SELECT COUNT(*) FROM dommes WHERE guild_id = $1) AS domme_count,
                    (
                        SELECT COUNT(DISTINCT sub_user_id)
                        FROM valid_sends
                        WHERE sub_user_id IS NOT NULL
                    ) AS sub_count,
                    (
                        SELECT COUNT(*)
                        FROM valid_sends
                        WHERE sub_user_id IS NULL
                    ) AS unclaimed_send_count,
                    (
                        SELECT COALESCE(SUM(amount_cents), 0)
                        FROM valid_sends
                        WHERE sub_user_id IS NULL
                    ) AS unclaimed_total_cents
                """,
                guild_id,
                include_test_sends,
                usernames,
                owner_test_user_id,
            )
        assert row is not None
        return LeaderboardSummary(
            total_cents=int(row["total_cents"] or 0),
            send_count=int(row["send_count"] or 0),
            domme_count=int(row["domme_count"] or 0),
            sub_count=int(row["sub_count"] or 0),
            unclaimed_send_count=int(row["unclaimed_send_count"] or 0),
            unclaimed_total_cents=int(row["unclaimed_total_cents"] or 0),
        )

    async def get_top_dommes(
        self,
        guild_id: int,
        *,
        limit: int = 10,
        include_test_sends: bool = False,
        test_gifter_usernames: tuple[str, ...] | list[str] = (),
        owner_test_user_id: int | None = None,
    ) -> list[LeaderboardEntry]:
        usernames = _normalized_test_usernames(test_gifter_usernames)
        async with self.database.acquire() as connection:
            rows = await connection.fetch(
                f"""
                {_valid_sends_cte()}
                SELECT
                    d.discord_user_id AS user_id,
                    COALESCE(SUM(v.amount_cents), 0) AS total_cents,
                    COUNT(v.id) AS send_count
                FROM dommes d
                LEFT JOIN valid_sends v
                    ON v.guild_id = d.guild_id
                    AND v.recipient_user_id = d.discord_user_id
                WHERE d.guild_id = $1
                GROUP BY d.discord_user_id
                ORDER BY total_cents DESC, send_count DESC, d.discord_user_id ASC
                LIMIT $5
                """,
                guild_id,
                include_test_sends,
                usernames,
                owner_test_user_id,
                limit,
            )
        return [
            LeaderboardEntry(
                label=f"<@{int(row['user_id'])}>",
                user_id=int(row["user_id"]),
                total_cents=int(row["total_cents"] or 0),
                send_count=int(row["send_count"] or 0),
            )
            for row in rows
        ]

    async def get_current_leader(
        self,
        guild_id: int,
        *,
        include_test_sends: bool = False,
        test_gifter_usernames: tuple[str, ...] | list[str] = (),
        owner_test_user_id: int | None = None,
    ) -> LeaderboardEntry | None:
        entries = await self.get_top_dommes(
            guild_id,
            limit=1,
            include_test_sends=include_test_sends,
            test_gifter_usernames=test_gifter_usernames,
            owner_test_user_id=owner_test_user_id,
        )
        return entries[0] if entries else None

    async def get_top_dommes_public(
        self,
        guild_id: int,
        *,
        limit: int = 10,
        include_test_sends: bool = False,
        test_gifter_usernames: tuple[str, ...] | list[str] = (),
        owner_test_user_id: int | None = None,
    ) -> list[LeaderboardEntry]:
        usernames = _normalized_test_usernames(test_gifter_usernames)
        async with self.database.acquire() as connection:
            rows = await connection.fetch(
                f"""
                {_valid_sends_cte()}
                SELECT
                    COALESCE(
                        NULLIF(TRIM(d.public_display_name), ''),
                        NULLIF(TRIM(d.throne_handle), ''),
                        'Registered Dom/me'
                    ) AS public_label,
                    d.discord_user_id AS user_id,
                    COALESCE(SUM(v.amount_cents), 0) AS total_cents,
                    COUNT(v.id) AS send_count
                FROM dommes d
                LEFT JOIN valid_sends v
                    ON v.guild_id = d.guild_id
                    AND v.recipient_user_id = d.discord_user_id
                WHERE d.guild_id = $1
                GROUP BY d.discord_user_id, d.public_display_name, d.throne_handle
                ORDER BY total_cents DESC, send_count DESC, d.discord_user_id ASC
                LIMIT $5
                """,
                guild_id,
                include_test_sends,
                usernames,
                owner_test_user_id,
                limit,
            )
        return [
            LeaderboardEntry(
                label=str(row["public_label"]),
                user_id=int(row["user_id"]),
                total_cents=int(row["total_cents"] or 0),
                send_count=int(row["send_count"] or 0),
            )
            for row in rows
        ]


    async def get_public_data_freshness(
        self,
        guild_id: int,
        *,
        include_test_sends: bool = False,
        test_gifter_usernames: tuple[str, ...] | list[str] = (),
        owner_test_user_id: int | None = None,
    ) -> datetime | None:
        usernames = _normalized_test_usernames(test_gifter_usernames)
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                f"""
                {_valid_sends_cte()}
                SELECT
                    MAX(COALESCE(v.sent_at, v.created_at)) AS latest_counted_send_at
                FROM valid_sends v
                """,
                guild_id,
                include_test_sends,
                usernames,
                owner_test_user_id,
            )
        if row is None:
            return None
        return row["latest_counted_send_at"]
    async def get_top_subs(
        self,
        guild_id: int,
        *,
        limit: int = 10,
        include_test_sends: bool = False,
        test_gifter_usernames: tuple[str, ...] | list[str] = (),
        owner_test_user_id: int | None = None,
    ) -> list[LeaderboardEntry]:
        usernames = _normalized_test_usernames(test_gifter_usernames)
        async with self.database.acquire() as connection:
            rows = await connection.fetch(
                f"""
                SELECT
                    sends.sub_user_id AS user_id,
                    MIN(subs.send_name) AS send_name,
                    COALESCE(SUM(sends.amount_cents), 0) AS total_cents,
                    COUNT(*) AS send_count
                FROM sends
                JOIN subs ON subs.id = sends.sub_id
                WHERE {_counted_send_filter("sends")}
                AND sends.sub_user_id IS NOT NULL
                GROUP BY sends.sub_user_id
                ORDER BY total_cents DESC, send_count DESC, sends.sub_user_id ASC
                LIMIT $5
                """,
                guild_id,
                include_test_sends,
                usernames,
                owner_test_user_id,
                limit,
            )
        return [
            LeaderboardEntry(
                label=f"<@{int(row['user_id'])}>",
                user_id=int(row["user_id"]),
                total_cents=int(row["total_cents"] or 0),
                send_count=int(row["send_count"] or 0),
            )
            for row in rows
        ]

    async def get_domme_stats(
        self,
        guild_id: int,
        *,
        domme_user_id: int,
        include_test_sends: bool = False,
        test_gifter_usernames: tuple[str, ...] | list[str] = (),
        owner_test_user_id: int | None = None,
    ) -> PersonalStatsSummary:
        usernames = _normalized_test_usernames(test_gifter_usernames)
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                f"""
                {_valid_sends_cte()}
                SELECT
                    COALESCE(SUM(amount_cents), 0) AS total_cents,
                    COUNT(*) AS send_count
                FROM valid_sends
                WHERE recipient_user_id = $5
                """,
                guild_id,
                include_test_sends,
                usernames,
                owner_test_user_id,
                domme_user_id,
            )
        assert row is not None
        return PersonalStatsSummary(
            total_cents=int(row["total_cents"] or 0),
            send_count=int(row["send_count"] or 0),
        )

    async def get_domme_rank(
        self,
        guild_id: int,
        *,
        domme_user_id: int,
        include_test_sends: bool = False,
        test_gifter_usernames: tuple[str, ...] | list[str] = (),
        owner_test_user_id: int | None = None,
    ) -> int | None:
        usernames = _normalized_test_usernames(test_gifter_usernames)
        async with self.database.acquire() as connection:
            value = await connection.fetchval(
                f"""
                {_valid_sends_cte()}
                SELECT ranked.rank
                FROM (
                    SELECT
                        d.discord_user_id AS user_id,
                        DENSE_RANK() OVER (
                            ORDER BY
                                COALESCE(SUM(v.amount_cents), 0) DESC,
                                COUNT(v.id) DESC,
                                d.discord_user_id ASC
                        ) AS rank
                    FROM dommes d
                    LEFT JOIN valid_sends v
                        ON v.guild_id = d.guild_id
                        AND v.recipient_user_id = d.discord_user_id
                    WHERE d.guild_id = $1
                    GROUP BY d.discord_user_id
                ) ranked
                WHERE ranked.user_id = $5
                """,
                guild_id,
                include_test_sends,
                usernames,
                owner_test_user_id,
                domme_user_id,
            )
        return int(value) if value is not None else None

    async def get_domme_latest_send(
        self,
        guild_id: int,
        *,
        domme_user_id: int,
        include_test_sends: bool = False,
        test_gifter_usernames: tuple[str, ...] | list[str] = (),
        owner_test_user_id: int | None = None,
    ) -> LatestTrackedSend | None:
        usernames = _normalized_test_usernames(test_gifter_usernames)
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                f"""
                {_valid_sends_cte()}
                SELECT *
                FROM valid_sends
                WHERE recipient_user_id = $5
                ORDER BY sent_at DESC, id DESC
                LIMIT 1
                """,
                guild_id,
                include_test_sends,
                usernames,
                owner_test_user_id,
                domme_user_id,
            )
        if row is None:
            return None
        return _build_latest_send(row)

    async def get_domme_top_sending_sub(
        self,
        guild_id: int,
        *,
        domme_user_id: int,
        include_test_sends: bool = False,
        test_gifter_usernames: tuple[str, ...] | list[str] = (),
        owner_test_user_id: int | None = None,
    ) -> LeaderboardEntry | None:
        usernames = _normalized_test_usernames(test_gifter_usernames)
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                f"""
                {_valid_sends_cte()}
                SELECT
                    sub_user_id,
                    COALESCE(sub_name, 'Sub with no nickname claimed') AS label,
                    COALESCE(SUM(amount_cents), 0) AS total_cents,
                    COUNT(*) AS send_count
                FROM valid_sends
                WHERE recipient_user_id = $5
                GROUP BY sub_user_id, COALESCE(sub_name, 'Sub with no nickname claimed')
                ORDER BY total_cents DESC, send_count DESC, COALESCE(sub_user_id, 0) ASC, label ASC
                LIMIT 1
                """,
                guild_id,
                include_test_sends,
                usernames,
                owner_test_user_id,
                domme_user_id,
            )
        if row is None:
            return None
        return LeaderboardEntry(
            label=str(row["label"]),
            user_id=int(row["sub_user_id"]) if row["sub_user_id"] is not None else None,
            total_cents=int(row["total_cents"] or 0),
            send_count=int(row["send_count"] or 0),
        )

    async def get_sub_stats(
        self,
        guild_id: int,
        *,
        sub_user_id: int,
        include_test_sends: bool = False,
        test_gifter_usernames: tuple[str, ...] | list[str] = (),
        owner_test_user_id: int | None = None,
    ) -> PersonalStatsSummary:
        usernames = _normalized_test_usernames(test_gifter_usernames)
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                f"""
                {_valid_sends_cte()}
                SELECT
                    COALESCE(SUM(amount_cents), 0) AS total_cents,
                    COUNT(*) AS send_count
                FROM valid_sends
                WHERE sub_user_id = $5
                """,
                guild_id,
                include_test_sends,
                usernames,
                owner_test_user_id,
                sub_user_id,
            )
        assert row is not None
        return PersonalStatsSummary(
            total_cents=int(row["total_cents"] or 0),
            send_count=int(row["send_count"] or 0),
        )

    async def get_sub_latest_send(
        self,
        guild_id: int,
        *,
        sub_user_id: int,
        include_test_sends: bool = False,
        test_gifter_usernames: tuple[str, ...] | list[str] = (),
        owner_test_user_id: int | None = None,
    ) -> LatestTrackedSend | None:
        usernames = _normalized_test_usernames(test_gifter_usernames)
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                f"""
                {_valid_sends_cte()}
                SELECT *
                FROM valid_sends
                WHERE sub_user_id = $5
                ORDER BY sent_at DESC, id DESC
                LIMIT 1
                """,
                guild_id,
                include_test_sends,
                usernames,
                owner_test_user_id,
                sub_user_id,
            )
        if row is None:
            return None
        return _build_latest_send(row)

    async def get_sub_top_domme(
        self,
        guild_id: int,
        *,
        sub_user_id: int,
        include_test_sends: bool = False,
        test_gifter_usernames: tuple[str, ...] | list[str] = (),
        owner_test_user_id: int | None = None,
    ) -> LeaderboardEntry | None:
        usernames = _normalized_test_usernames(test_gifter_usernames)
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                f"""
                {_valid_sends_cte()}
                SELECT
                    recipient_user_id AS domme_user_id,
                    COALESCE(SUM(amount_cents), 0) AS total_cents,
                    COUNT(*) AS send_count
                FROM valid_sends
                WHERE sub_user_id = $5
                GROUP BY recipient_user_id
                ORDER BY total_cents DESC, send_count DESC, recipient_user_id ASC
                LIMIT 1
                """,
                guild_id,
                include_test_sends,
                usernames,
                owner_test_user_id,
                sub_user_id,
            )
        if row is None:
            return None
        domme_user_id = int(row["domme_user_id"])
        return LeaderboardEntry(
            label=f"<@{domme_user_id}>",
            user_id=domme_user_id,
            total_cents=int(row["total_cents"] or 0),
            send_count=int(row["send_count"] or 0),
        )

    async def diagnose(
        self,
        guild_id: int,
        *,
        include_test_sends: bool = False,
        test_gifter_usernames: tuple[str, ...] | list[str] = (),
        owner_test_user_id: int | None = None,
        limit: int = 10,
    ) -> LeaderboardDiagnostics:
        usernames = _normalized_test_usernames(test_gifter_usernames)
        async with self.database.acquire() as connection:
            registered_dommes = int(
                await connection.fetchval(
                    "SELECT COUNT(*) FROM dommes WHERE guild_id = $1",
                    guild_id,
                )
                or 0
            )

            counted_sends = int(
                await connection.fetchval(
                    f"""
                    SELECT COUNT(*)
                    FROM sends s
                    WHERE {_counted_send_filter("s")}
                    """,
                    guild_id,
                    include_test_sends,
                    usernames,
                    owner_test_user_id,
                )
                or 0
            )

            excluded_not_posted = int(
                await connection.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM sends
                    WHERE guild_id = $1
                    AND discord_post_status <> 'posted'
                    """,
                    guild_id,
                )
                or 0
            )
            excluded_private = int(
                await connection.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM sends
                    WHERE guild_id = $1
                    AND discord_post_status = 'posted'
                    AND is_private = true
                    """,
                    guild_id,
                )
                or 0
            )
            excluded_test_send = 0
            if not include_test_sends:
                excluded_test_send = int(
                    await connection.fetchval(
                        """
                        SELECT COUNT(*)
                        FROM sends
                        WHERE guild_id = $1
                        AND discord_post_status = 'posted'
                        AND is_private = false
                        AND (
                            COALESCE(is_test_send, false)
                            OR lower(COALESCE(sub_name, '')) = ANY($2::text[])
                        )
                        AND ($3::bigint IS NULL OR domme_user_id <> $3)
                        """,
                        guild_id,
                        usernames,
                        owner_test_user_id,
                    )
                    or 0
                )

            excluded_domme_mismatch = int(
                await connection.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM sends s
                    LEFT JOIN dommes d_by_id
                        ON d_by_id.id = s.domme_id
                        AND d_by_id.guild_id = s.guild_id
                    LEFT JOIN dommes d_by_user
                        ON d_by_user.guild_id = s.guild_id
                        AND d_by_user.discord_user_id = s.domme_user_id
                    WHERE s.guild_id = $1
                    AND s.discord_post_status = 'posted'
                    AND s.is_private = false
                    AND d_by_id.id IS NULL
                    AND d_by_user.id IS NULL
                    """,
                    guild_id,
                )
                or 0
            )

            excluded_guild_mismatch = int(
                await connection.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM sends s
                    JOIN dommes d
                        ON d.discord_user_id = s.domme_user_id
                    WHERE s.guild_id = $1
                    AND d.guild_id <> s.guild_id
                    """,
                    guild_id,
                )
                or 0
            )

            rows = await connection.fetch(
                f"""
                {_valid_sends_cte()}
                SELECT
                    d.discord_user_id AS user_id,
                    COALESCE(SUM(v.amount_cents), 0) AS total_cents,
                    COUNT(v.id) AS send_count
                FROM dommes d
                LEFT JOIN valid_sends v
                    ON v.guild_id = d.guild_id
                    AND v.recipient_user_id = d.discord_user_id
                WHERE d.guild_id = $1
                GROUP BY d.discord_user_id
                ORDER BY total_cents DESC, send_count DESC, d.discord_user_id ASC
                LIMIT $5
                """,
                guild_id,
                include_test_sends,
                usernames,
                owner_test_user_id,
                limit,
            )
            domme_rows = [
                LeaderboardEntry(
                    label=f"<@{int(row['user_id'])}>",
                    user_id=int(row["user_id"]),
                    total_cents=int(row["total_cents"] or 0),
                    send_count=int(row["send_count"] or 0),
                )
                for row in rows
            ]

            unmatched_rows = await connection.fetch(
                """
                SELECT s.id, s.domme_user_id, s.guild_id
                FROM sends s
                LEFT JOIN dommes d_by_id
                    ON d_by_id.id = s.domme_id
                    AND d_by_id.guild_id = s.guild_id
                LEFT JOIN dommes d_by_user
                    ON d_by_user.guild_id = s.guild_id
                    AND d_by_user.discord_user_id = s.domme_user_id
                WHERE s.guild_id = $1
                AND d_by_id.id IS NULL
                AND d_by_user.id IS NULL
                ORDER BY s.id ASC
                LIMIT 20
                """,
                guild_id,
            )

        excluded_sends = (
            excluded_not_posted
            + excluded_private
            + excluded_test_send
            + excluded_domme_mismatch
        )
        return LeaderboardDiagnostics(
            guild_id=guild_id,
            registered_dommes=registered_dommes,
            counted_sends=counted_sends,
            excluded_sends=excluded_sends,
            excluded_not_posted=excluded_not_posted,
            excluded_private=excluded_private,
            excluded_test_send=excluded_test_send,
            excluded_domme_mismatch=excluded_domme_mismatch,
            excluded_guild_mismatch=excluded_guild_mismatch,
            domme_rows=domme_rows,
            unmatched_sends=[
                (int(row["id"]), int(row["domme_user_id"]), int(row["guild_id"]))
                for row in unmatched_rows
            ],
        )

    async def get_message(self, guild_id: int, message_key: str) -> LeaderboardMessageRef | None:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT *
                FROM vib_leaderboard
                WHERE guild_id = $1
                AND leaderboard_key = $2
                """,
                guild_id,
                message_key,
            )
        if row is None:
            return None
        return _build_message_ref(row)

    async def upsert_message(
        self,
        *,
        guild_id: int,
        message_key: str,
        leaderboard_type: str | None,
        channel_id: int,
        message_id: int,
    ) -> LeaderboardMessageRef:
        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                """
                INSERT INTO vib_leaderboard (
                    guild_id,
                    leaderboard_key,
                    leaderboard_type,
                    channel_id,
                    message_id,
                    title
                )
                VALUES ($1, $2, $3, $4, $5, 'Send Leaderboard')
                ON CONFLICT (guild_id, leaderboard_key) DO UPDATE SET
                    leaderboard_type = EXCLUDED.leaderboard_type,
                    channel_id = EXCLUDED.channel_id,
                    message_id = EXCLUDED.message_id,
                    updated_at = now()
                RETURNING *
                """,
                guild_id,
                message_key,
                leaderboard_type,
                channel_id,
                message_id,
            )
        assert row is not None
        return _build_message_ref(row)
