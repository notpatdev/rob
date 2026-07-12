from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite


@dataclass(frozen=True)
class ThroneCreator:
    id: int
    guild_id: str
    discord_user_id: str
    throne_handle: str
    throne_creator_id: str
    hide_own_purchases: bool | None
    tracking_mode: str
    webhook_secret: str
    webhook_connected_at: str | None
    overlay_detected: bool
    last_overlay_check_at: str | None
    last_successful_event_at: str | None
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "ThroneCreator":
        keys = row.keys()
        return cls(
            id=int(row["id"]),
            guild_id=row["guild_id"],
            discord_user_id=row["discord_user_id"],
            throne_handle=row["throne_handle"],
            throne_creator_id=row["throne_creator_id"],
            hide_own_purchases=bool(row["hide_own_purchases"])
            if row["hide_own_purchases"] is not None
            else None,
            tracking_mode=row["tracking_mode"],
            webhook_secret=row["webhook_secret"],
            webhook_connected_at=row["webhook_connected_at"],
            overlay_detected=bool(row["overlay_detected"]),
            last_overlay_check_at=row["last_overlay_check_at"]
            if "last_overlay_check_at" in keys
            else None,
            last_successful_event_at=row["last_successful_event_at"]
            if "last_successful_event_at" in keys
            else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


@dataclass(frozen=True)
class ThroneWishlistItem:
    creator_id: str
    wishlist_item_id: str
    item_name: str | None
    item_image_url: str | None
    amount_usd: float
    currency: str | None
    is_available: bool | None
    last_seen_at: str

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "ThroneWishlistItem":
        return cls(
            creator_id=str(row["creator_id"]),
            wishlist_item_id=str(row["wishlist_item_id"]),
            item_name=row["item_name"],
            item_image_url=row["item_image_url"],
            amount_usd=float(row["amount_usd"]),
            currency=row["currency"],
            is_available=bool(row["is_available"])
            if row["is_available"] is not None
            else None,
            last_seen_at=str(row["last_seen_at"]),
        )


@dataclass(frozen=True)
class ThroneWebhookSend:
    discord_user_id: str
    sub_name: str | None
    amount_usd: float
    is_private: bool
    sent_at: str


@dataclass(frozen=True)
class EventSubTotalRow:
    user_id: int
    total_usd: float
    send_count: int


@dataclass(frozen=True)
class EventDommeTotalRow:
    user_id: int
    total_usd: float
    send_count: int


@dataclass(frozen=True)
class UnclaimedSendRow:
    sub_name: str
    total_usd: float
    send_count: int


@dataclass(frozen=True)
class SendSummary:
    total_usd: float
    send_count: int


@dataclass(frozen=True)
class EventDommeRegistration:
    user_id: int
    throne_url: str
    registered_at: str

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "EventDommeRegistration":
        return cls(
            user_id=int(row["user_id"]),
            throne_url=row["throne_url"],
            registered_at=row["registered_at"],
        )


@dataclass(frozen=True)
class EventSubRegistration:
    user_id: int
    sub_name: str
    registered_at: str

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "EventSubRegistration":
        return cls(
            user_id=int(row["user_id"]),
            sub_name=row["sub_name"],
            registered_at=row["registered_at"],
        )


@dataclass(frozen=True)
class EventState:
    event_key: str
    event_name: str | None
    is_active: bool
    starts_at: str | None
    ends_at: str | None
    ended_at: str | None
    started_by: int | None
    ended_by: int | None
    report_posted_at: str | None

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "EventState":
        return cls(
            event_key=row["event_key"],
            event_name=row["event_name"] if "event_name" in row.keys() else None,
            is_active=bool(row["is_active"]),
            starts_at=row["starts_at"],
            ends_at=row["ends_at"],
            ended_at=row["ended_at"],
            started_by=int(row["started_by"])
            if row["started_by"] is not None
            else None,
            ended_by=int(row["ended_by"]) if row["ended_by"] is not None else None,
            report_posted_at=row["report_posted_at"]
            if "report_posted_at" in row.keys()
            else None,
        )


@dataclass(frozen=True)
class EventSend:
    id: int
    domme_user_id: int
    sub_name: str | None
    claimed_sub_user_id: int | None
    amount_usd: float
    item_name: str | None
    item_image_url: str | None
    logged_by: int
    sent_at: str
    external_id: str | None
    is_private: bool
    seeded: bool
    event_key: str | None

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "EventSend":
        return cls(
            id=int(row["id"]),
            domme_user_id=int(row["domme_user_id"]),
            sub_name=row["sub_name"],
            claimed_sub_user_id=int(row["claimed_sub_user_id"])
            if row["claimed_sub_user_id"] is not None
            else None,
            amount_usd=float(row["amount_usd"]),
            item_name=row["item_name"],
            item_image_url=row["item_image_url"],
            logged_by=int(row["logged_by"]),
            sent_at=row["sent_at"],
            external_id=row["external_id"],
            is_private=bool(row["is_private"]),
            seeded=bool(row["seeded"]),
            event_key=row["event_key"] if "event_key" in row.keys() else None,
        )


@dataclass(frozen=True)
class SendRequest:
    id: int
    sub_user_id: int
    domme_user_id: int
    amount_usd: float
    method: str
    note: str | None
    status: str
    created_at: str
    resolved_at: str | None

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "SendRequest":
        return cls(
            id=int(row["id"]),
            sub_user_id=int(row["sub_user_id"]),
            domme_user_id=int(row["domme_user_id"]),
            amount_usd=float(row["amount_usd"]),
            method=str(row["method"]),
            note=row["note"],
            status=str(row["status"]),
            created_at=str(row["created_at"]),
            resolved_at=row["resolved_at"],
        )


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


_CONFIG_INT_KEYS = frozenset(
    {
        "guild_id",
        "registration_channel_id",
        "leaderboard_channel_id",
        "send_track_channel_id",
        "event_report_channel_id",
        "moderation_role_id",
        "domme_role_id",
        "submissive_role_id",
        "event_ban_role_id",
    }
)


class Database:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._connection: aiosqlite.Connection | None = None

    @property
    def connection(self) -> aiosqlite.Connection:
        if self._connection is None:
            raise RuntimeError("Database has not been initialized")
        return self._connection

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = await aiosqlite.connect(self.path)
        self._connection.row_factory = aiosqlite.Row
        await self.connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;

            CREATE TABLE IF NOT EXISTS event_messages (
                message_key TEXT PRIMARY KEY,
                message_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS event_state (
                event_key TEXT PRIMARY KEY,
                event_name TEXT,
                is_active INTEGER NOT NULL DEFAULT 0,
                starts_at TEXT,
                ends_at TEXT,
                ended_at TEXT,
                started_by INTEGER,
                ended_by INTEGER,
                report_posted_at TEXT
            );

            CREATE TABLE IF NOT EXISTS event_dommes (
                user_id INTEGER PRIMARY KEY,
                throne_url TEXT NOT NULL,
                registered_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS event_subs (
                user_id INTEGER PRIMARY KEY,
                sub_name TEXT NOT NULL COLLATE NOCASE UNIQUE,
                registered_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS event_sends (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                domme_user_id INTEGER NOT NULL,
                sub_name TEXT COLLATE NOCASE,
                claimed_sub_user_id INTEGER,
                amount_usd REAL NOT NULL,
                item_name TEXT,
                item_image_url TEXT,
                logged_by INTEGER NOT NULL,
                sent_at TEXT NOT NULL,
                external_id TEXT,
                is_private INTEGER NOT NULL DEFAULT 0,
                seeded INTEGER NOT NULL DEFAULT 0,
                event_key TEXT
            );

            CREATE TABLE IF NOT EXISTS rob_blacklist (
                discord_user_id TEXT PRIMARY KEY,
                reason TEXT,
                created_at TEXT NOT NULL,
                created_by TEXT
            );

            CREATE TABLE IF NOT EXISTS send_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sub_user_id INTEGER NOT NULL,
                domme_user_id INTEGER NOT NULL,
                amount_usd REAL NOT NULL,
                method TEXT NOT NULL,
                note TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                resolved_at TEXT
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_event_sends_external_id
            ON event_sends(external_id)
            WHERE external_id IS NOT NULL;

            CREATE INDEX IF NOT EXISTS idx_event_sends_domme
            ON event_sends(domme_user_id);

            CREATE INDEX IF NOT EXISTS idx_event_sends_sub
            ON event_sends(sub_name);

            CREATE INDEX IF NOT EXISTS idx_event_sends_event_key
            ON event_sends(event_key);

            CREATE INDEX IF NOT EXISTS idx_send_requests_rate_limit
            ON send_requests(sub_user_id, domme_user_id, created_at);

            CREATE TABLE IF NOT EXISTS throne_creators (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                discord_user_id TEXT NOT NULL,
                throne_handle TEXT NOT NULL,
                throne_creator_id TEXT NOT NULL,
                hide_own_purchases INTEGER,
                tracking_mode TEXT NOT NULL DEFAULT 'disabled',
                webhook_secret TEXT NOT NULL,
                webhook_connected_at TEXT,
                overlay_detected INTEGER NOT NULL DEFAULT 0,
                last_overlay_check_at TEXT,
                last_successful_event_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(guild_id, throne_handle)
            );

            CREATE INDEX IF NOT EXISTS idx_throne_creators_creator_id
            ON throne_creators(throne_creator_id);

            CREATE TABLE IF NOT EXISTS throne_wishlist_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_id TEXT NOT NULL,
                wishlist_item_id TEXT NOT NULL,
                item_name TEXT,
                item_image_url TEXT,
                amount_usd REAL NOT NULL,
                currency TEXT,
                is_available INTEGER,
                last_seen_at TEXT NOT NULL,
                UNIQUE(creator_id, wishlist_item_id)
            );

            CREATE INDEX IF NOT EXISTS idx_throne_wishlist_items_creator
            ON throne_wishlist_items(creator_id);

            CREATE TABLE IF NOT EXISTS bot_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        await self._migrate_schema()
        await self.connection.commit()

    async def _migrate_schema(self) -> None:
        await self._ensure_column("event_state", "event_name", "TEXT")
        await self._ensure_column("event_state", "report_posted_at", "TEXT")
        await self._ensure_column("event_sends", "event_key", "TEXT")
        await self._ensure_column("event_sends", "event_id", "TEXT")
        await self._ensure_column("event_sends", "fallback_event_hash", "TEXT")
        await self._ensure_column("event_sends", "source", "TEXT")
        await self._ensure_column(
            "bot_config", "value", "TEXT NOT NULL", allow_existing=True
        )
        await self.connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_event_sends_event_key
            ON event_sends(event_key)
            """
        )
        await self.connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_event_sends_event_id
            ON event_sends(event_id)
            WHERE event_id IS NOT NULL
            """
        )
        await self.connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_event_sends_fallback_hash
            ON event_sends(fallback_event_hash)
            WHERE fallback_event_hash IS NOT NULL
            """
        )
        # Backfill source = 'overlay' for rows inserted before this column existed.
        await self.connection.execute(
            "UPDATE event_sends SET source = 'overlay' WHERE source IS NULL"
        )

    async def _column_names(self, table_name: str) -> set[str]:
        async with self.connection.execute(
            f"PRAGMA table_info({table_name})"
        ) as cursor:
            rows = await cursor.fetchall()
        return {str(row["name"]) for row in rows}

    async def _ensure_column(
        self,
        table_name: str,
        column_name: str,
        column_type: str,
        *,
        allow_existing: bool = False,
    ) -> None:
        columns = await self._column_names(table_name)
        if column_name in columns:
            return
        if allow_existing and not columns:
            return
        await self.connection.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"
        )

    async def close(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None

    async def get_event_sub_totals(
        self,
        *,
        limit: int = 10,
        offset: int = 0,
        event_key: str | None = None,
    ) -> list[EventSubTotalRow]:
        where_sql, params = self._event_filter(event_key)
        query = f"""
            SELECT
                claimed_sub_user_id AS user_id,
                SUM(CASE WHEN is_private = 0 THEN amount_usd ELSE 0 END) AS total_usd,
                COUNT(*) AS send_count
            FROM event_sends
            WHERE claimed_sub_user_id IS NOT NULL
            {where_sql}
            GROUP BY claimed_sub_user_id
            ORDER BY total_usd DESC, send_count DESC, claimed_sub_user_id ASC
            LIMIT ? OFFSET ?
        """
        async with self.connection.execute(query, (*params, limit, offset)) as cursor:
            rows = await cursor.fetchall()
        return [
            EventSubTotalRow(
                user_id=int(row["user_id"]),
                total_usd=float(row["total_usd"] or 0.0),
                send_count=int(row["send_count"]),
            )
            for row in rows
        ]

    async def save_event_domme(
        self,
        *,
        user_id: int,
        throne_url: str,
    ) -> None:
        async with self.connection.execute(
            """
            INSERT INTO event_dommes (user_id, throne_url, registered_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                throne_url = excluded.throne_url,
                registered_at = event_dommes.registered_at
            """,
            (user_id, throne_url, _utc_now()),
        ):
            pass
        await self.connection.commit()

    async def get_all_event_dommes(self) -> list[EventDommeRegistration]:
        async with self.connection.execute(
            """
            SELECT user_id, throne_url, registered_at
            FROM event_dommes
            ORDER BY registered_at ASC
            """
        ) as cursor:
            rows = await cursor.fetchall()
        return [EventDommeRegistration.from_row(row) for row in rows]

    async def get_all_event_subs(self) -> list[EventSubRegistration]:
        async with self.connection.execute(
            """
            SELECT user_id, sub_name, registered_at
            FROM event_subs
            ORDER BY registered_at ASC
            """
        ) as cursor:
            rows = await cursor.fetchall()
        return [EventSubRegistration.from_row(row) for row in rows]

    async def save_event_sub(
        self,
        *,
        user_id: int,
        sub_name: str,
    ) -> None:
        async with self.connection.execute(
            """
            INSERT INTO event_subs (user_id, sub_name, registered_at)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                sub_name = excluded.sub_name,
                registered_at = event_subs.registered_at
            """,
            (user_id, sub_name, _utc_now()),
        ):
            pass
        await self.connection.execute(
            """
            UPDATE event_sends
            SET claimed_sub_user_id = ?
            WHERE sub_name = ? COLLATE NOCASE
            AND claimed_sub_user_id IS NULL
            """,
            (user_id, sub_name),
        )
        await self.connection.commit()

    async def get_event_sub_by_name(
        self, *, sub_name: str
    ) -> EventSubRegistration | None:
        async with self.connection.execute(
            """
            SELECT user_id, sub_name, registered_at
            FROM event_subs
            WHERE sub_name = ? COLLATE NOCASE
            """,
            (sub_name,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return EventSubRegistration.from_row(row)

    async def count_event_sub_registrations(self) -> int:
        async with self.connection.execute(
            "SELECT COUNT(*) AS count FROM event_subs"
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["count"]) if row is not None else 0

    async def count_event_ranked_subs(self, *, event_key: str | None = None) -> int:
        where_sql, params = self._event_filter(event_key)
        query = f"""
            SELECT COUNT(*) AS count
            FROM (
                SELECT claimed_sub_user_id
                FROM event_sends
                WHERE claimed_sub_user_id IS NOT NULL
                {where_sql}
                GROUP BY claimed_sub_user_id
            )
        """
        async with self.connection.execute(query, params) as cursor:
            row = await cursor.fetchone()
        return int(row["count"]) if row is not None else 0

    async def get_event_sub_rank(
        self, *, user_id: int, event_key: str | None = None
    ) -> int | None:
        where_sql, params = self._event_filter(event_key)
        query = f"""
            SELECT rank FROM (
                SELECT
                    claimed_sub_user_id AS user_id,
                    ROW_NUMBER() OVER (
                        ORDER BY
                            SUM(CASE WHEN is_private = 0 THEN amount_usd ELSE 0 END) DESC,
                            COUNT(*) DESC,
                            claimed_sub_user_id ASC
                    ) AS rank
                FROM event_sends
                WHERE claimed_sub_user_id IS NOT NULL
                {where_sql}
                GROUP BY claimed_sub_user_id
            ) ranked
            WHERE user_id = ?
        """
        async with self.connection.execute(query, (*params, user_id)) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return int(row["rank"])

    async def get_event_sub_total(
        self,
        *,
        user_id: int,
        event_key: str | None = None,
    ) -> EventSubTotalRow:
        where_sql, params = self._event_filter(event_key)
        query = f"""
            SELECT
                claimed_sub_user_id AS user_id,
                COALESCE(SUM(CASE WHEN is_private = 0 THEN amount_usd ELSE 0 END), 0) AS total_usd,
                COUNT(*) AS send_count
            FROM event_sends
            WHERE claimed_sub_user_id = ?
            {where_sql}
            GROUP BY claimed_sub_user_id
        """
        async with self.connection.execute(query, (user_id, *params)) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return EventSubTotalRow(user_id=user_id, total_usd=0.0, send_count=0)
        return EventSubTotalRow(
            user_id=int(row["user_id"]),
            total_usd=float(row["total_usd"] or 0.0),
            send_count=int(row["send_count"]),
        )

    async def get_event_unclaimed_rank(
        self,
        *,
        sub_name: str | None,
        event_key: str | None = None,
    ) -> int | None:
        where_sql, params = self._event_filter(event_key)
        target_key = "__anonymous__"
        if sub_name is not None:
            trimmed = sub_name.strip()
            if trimmed:
                target_key = trimmed.casefold()
        query = f"""
            WITH grouped AS (
                SELECT
                    COALESCE(NULLIF(LOWER(TRIM(sub_name)), ''), '__anonymous__') AS sender_key,
                    SUM(CASE WHEN is_private = 0 THEN amount_usd ELSE 0 END) AS total_usd,
                    COUNT(*) AS send_count
                FROM event_sends
                WHERE claimed_sub_user_id IS NULL
                {where_sql}
                GROUP BY sender_key
            )
            SELECT rank FROM (
                SELECT
                    sender_key,
                    ROW_NUMBER() OVER (
                        ORDER BY total_usd DESC, send_count DESC, sender_key ASC
                    ) AS rank
                FROM grouped
            ) ranked
            WHERE sender_key = ?
        """
        async with self.connection.execute(query, (*params, target_key)) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return int(row["rank"])

    async def get_event_unclaimed_total(self, *, event_key: str | None = None) -> float:
        where_sql, params = self._event_filter(event_key)
        query = f"""
            SELECT COALESCE(SUM(CASE WHEN is_private = 0 THEN amount_usd ELSE 0 END), 0) AS total_usd
            FROM event_sends
            WHERE claimed_sub_user_id IS NULL
            {where_sql}
        """
        async with self.connection.execute(query, params) as cursor:
            row = await cursor.fetchone()
        return float(row["total_usd"]) if row is not None else 0.0

    async def get_unclaimed_send_rows(
        self,
        *,
        limit: int = 10,
        event_key: str | None = None,
    ) -> list[UnclaimedSendRow]:
        where_sql, params = self._event_filter(event_key)
        query = f"""
            WITH trimmed_sends AS (
                SELECT
                    TRIM(sub_name) AS sub_name,
                    amount_usd,
                    is_private
                FROM event_sends
                WHERE claimed_sub_user_id IS NULL
                  AND sub_name IS NOT NULL
                  {where_sql}
            )
            SELECT
                MIN(sub_name) AS sub_name,
                SUM(CASE WHEN is_private = 0 THEN amount_usd ELSE 0 END) AS total_usd,
                COUNT(*) AS send_count
            FROM trimmed_sends
            WHERE sub_name != ''
            GROUP BY sub_name COLLATE NOCASE
            ORDER BY total_usd DESC, send_count DESC, sub_name COLLATE NOCASE ASC
            LIMIT ?
        """
        async with self.connection.execute(query, (*params, limit)) as cursor:
            rows = await cursor.fetchall()
        return [
            UnclaimedSendRow(
                sub_name=str(row["sub_name"]),
                total_usd=float(row["total_usd"] or 0.0),
                send_count=int(row["send_count"]),
            )
            for row in rows
        ]

    async def get_event_domme_totals(
        self, *, event_key: str | None = None
    ) -> list[EventDommeTotalRow]:
        where_sql, params = self._event_filter(event_key)
        query = f"""
            SELECT
                domme_user_id AS user_id,
                SUM(CASE WHEN is_private = 0 THEN amount_usd ELSE 0 END) AS total_usd,
                COUNT(*) AS send_count
            FROM event_sends
            WHERE 1 = 1
            {where_sql}
            GROUP BY domme_user_id
            ORDER BY total_usd DESC, send_count DESC, domme_user_id ASC
        """
        async with self.connection.execute(query, params) as cursor:
            rows = await cursor.fetchall()
        return [
            EventDommeTotalRow(
                user_id=int(row["user_id"]),
                total_usd=float(row["total_usd"] or 0.0),
                send_count=int(row["send_count"]),
            )
            for row in rows
        ]

    async def get_event_domme_total(
        self,
        *,
        user_id: int,
        event_key: str | None = None,
    ) -> EventDommeTotalRow:
        where_sql, params = self._event_filter(event_key)
        query = f"""
            SELECT
                domme_user_id AS user_id,
                COALESCE(SUM(CASE WHEN is_private = 0 THEN amount_usd ELSE 0 END), 0) AS total_usd,
                COUNT(*) AS send_count
            FROM event_sends
            WHERE domme_user_id = ?
            {where_sql}
            GROUP BY domme_user_id
        """
        async with self.connection.execute(query, (user_id, *params)) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return EventDommeTotalRow(user_id=user_id, total_usd=0.0, send_count=0)
        return EventDommeTotalRow(
            user_id=int(row["user_id"]),
            total_usd=float(row["total_usd"] or 0.0),
            send_count=int(row["send_count"]),
        )

    async def get_event_domme_rank(
        self, *, user_id: int, event_key: str | None = None
    ) -> int | None:
        where_sql, params = self._event_filter(event_key)
        query = f"""
            SELECT rank FROM (
                SELECT
                    domme_user_id AS user_id,
                    ROW_NUMBER() OVER (
                        ORDER BY
                            SUM(CASE WHEN is_private = 0 THEN amount_usd ELSE 0 END) DESC,
                            COUNT(*) DESC,
                            domme_user_id ASC
                    ) AS rank
                FROM event_sends
                WHERE 1 = 1
                {where_sql}
                GROUP BY domme_user_id
            ) ranked
            WHERE user_id = ?
        """
        async with self.connection.execute(query, (*params, user_id)) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return int(row["rank"])

    async def get_send_summary(self, *, event_key: str | None = None) -> SendSummary:
        where_sql, params = self._event_filter(event_key)
        query = f"""
            SELECT
                COALESCE(SUM(CASE WHEN is_private = 0 THEN amount_usd ELSE 0 END), 0) AS total_usd,
                COUNT(*) AS send_count
            FROM event_sends
            WHERE 1 = 1
            {where_sql}
        """
        async with self.connection.execute(query, params) as cursor:
            row = await cursor.fetchone()
        return SendSummary(
            total_usd=float(row["total_usd"] or 0.0) if row is not None else 0.0,
            send_count=int(row["send_count"]) if row is not None else 0,
        )

    async def get_event_message(self, *, message_key: str) -> tuple[int, int] | None:
        async with self.connection.execute(
            "SELECT message_id, channel_id FROM event_messages WHERE message_key = ?",
            (message_key,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return int(row["message_id"]), int(row["channel_id"])

    async def upsert_event_message(
        self,
        *,
        message_key: str,
        message_id: int,
        channel_id: int,
    ) -> None:
        async with self.connection.execute(
            """
            INSERT INTO event_messages (message_key, message_id, channel_id)
            VALUES (?, ?, ?)
            ON CONFLICT(message_key) DO UPDATE SET
                message_id = excluded.message_id,
                channel_id = excluded.channel_id
            """,
            (message_key, message_id, channel_id),
        ):
            pass
        await self.connection.commit()

    async def get_active_event_state(self) -> EventState | None:
        async with self.connection.execute(
            """
            SELECT event_key, event_name, is_active, starts_at, ends_at, ended_at, started_by, ended_by, report_posted_at
            FROM event_state
            WHERE is_active = 1
            ORDER BY starts_at ASC, event_key ASC
            LIMIT 1
            """
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return EventState.from_row(row)

    async def get_event_state(self, *, event_key: str) -> EventState | None:
        async with self.connection.execute(
            """
            SELECT event_key, event_name, is_active, starts_at, ends_at, ended_at, started_by, ended_by, report_posted_at
            FROM event_state
            WHERE event_key = ?
            """,
            (event_key,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return EventState.from_row(row)

    async def get_pending_event_reports(self) -> list[EventState]:
        async with self.connection.execute(
            """
            SELECT event_key, event_name, is_active, starts_at, ends_at, ended_at, started_by, ended_by, report_posted_at
            FROM event_state
            WHERE is_active = 0
              AND ended_at IS NOT NULL
              AND report_posted_at IS NULL
            ORDER BY ended_at ASC, event_key ASC
            """
        ) as cursor:
            rows = await cursor.fetchall()
        return [EventState.from_row(row) for row in rows]

    async def activate_event(
        self,
        *,
        event_key: str,
        event_name: str,
        starts_at: str | None,
        ends_at: str | None,
        started_by: int | None,
    ) -> EventState:
        async with self.connection.execute(
            """
            INSERT INTO event_state (
                event_key,
                event_name,
                is_active,
                starts_at,
                ends_at,
                ended_at,
                started_by,
                ended_by,
                report_posted_at
            )
            VALUES (?, ?, 1, ?, ?, NULL, ?, NULL, NULL)
            ON CONFLICT(event_key) DO UPDATE SET
                event_name = excluded.event_name,
                is_active = 1,
                starts_at = excluded.starts_at,
                ends_at = excluded.ends_at,
                ended_at = NULL,
                started_by = excluded.started_by,
                ended_by = NULL,
                report_posted_at = NULL
            """,
            (event_key, event_name, starts_at, ends_at, started_by),
        ):
            pass
        await self.connection.commit()
        state = await self.get_event_state(event_key=event_key)
        if state is None:
            raise RuntimeError(f"Failed to activate event state for {event_key}")
        return state

    async def end_event(
        self,
        *,
        event_key: str,
        ended_by: int | None,
        ended_at: str | None = None,
    ) -> EventState:
        ended_at_value = ended_at or _utc_now()
        async with self.connection.execute(
            """
            INSERT INTO event_state (
                event_key,
                event_name,
                is_active,
                starts_at,
                ends_at,
                ended_at,
                started_by,
                ended_by,
                report_posted_at
            )
            VALUES (?, NULL, 0, NULL, NULL, ?, NULL, ?, NULL)
            ON CONFLICT(event_key) DO UPDATE SET
                is_active = 0,
                ended_at = excluded.ended_at,
                ended_by = excluded.ended_by
            """,
            (event_key, ended_at_value, ended_by),
        ):
            pass
        await self.connection.commit()
        state = await self.get_event_state(event_key=event_key)
        if state is None:
            raise RuntimeError(f"Failed to end event state for {event_key}")
        return state

    async def mark_event_report_posted(
        self, *, event_key: str, posted_at: str | None = None
    ) -> None:
        async with self.connection.execute(
            """
            UPDATE event_state
            SET report_posted_at = ?
            WHERE event_key = ?
            """,
            (posted_at or _utc_now(), event_key),
        ):
            pass
        await self.connection.commit()

    async def log_event_send(
        self,
        *,
        domme_user_id: int,
        sub_name: str | None,
        amount_usd: float,
        item_name: str | None,
        item_image_url: str | None,
        logged_by: int,
        external_id: str | None = None,
        event_id: str | None = None,
        fallback_event_hash: str | None = None,
        source: str | None = None,
        is_private: bool = False,
        seeded: bool = False,
        sent_at: str | None = None,
        event_key: str | None = None,
    ) -> int | None:
        claimed_sub_user_id: int | None = None
        if sub_name:
            sub = await self.get_event_sub_by_name(sub_name=sub_name)
            if sub is not None:
                claimed_sub_user_id = sub.user_id

        try:
            async with self.connection.execute(
                """
                INSERT INTO event_sends (
                    domme_user_id,
                    sub_name,
                    claimed_sub_user_id,
                    amount_usd,
                    item_name,
                    item_image_url,
                    logged_by,
                    sent_at,
                    external_id,
                    event_id,
                    fallback_event_hash,
                    source,
                    is_private,
                    seeded,
                    event_key
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    domme_user_id,
                    sub_name,
                    claimed_sub_user_id,
                    amount_usd,
                    item_name,
                    item_image_url,
                    logged_by,
                    sent_at or _utc_now(),
                    external_id,
                    event_id,
                    fallback_event_hash,
                    source,
                    int(bool(is_private)),
                    int(bool(seeded)),
                    event_key,
                ),
            ) as cursor:
                send_id = int(cursor.lastrowid)
        except aiosqlite.IntegrityError:
            # Duplicate detected via unique index (external_id, event_id, or fallback_event_hash).
            return None
        await self.connection.commit()
        return send_id

    async def get_event_send(self, *, send_id: int) -> EventSend | None:
        async with self.connection.execute(
            "SELECT * FROM event_sends WHERE id = ?",
            (send_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return EventSend.from_row(row)

    async def get_known_event_external_ids_for_domme(
        self,
        *,
        domme_user_id: int,
    ) -> set[str]:
        async with self.connection.execute(
            """
            SELECT external_id
            FROM event_sends
            WHERE domme_user_id = ? AND external_id IS NOT NULL
            """,
            (domme_user_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return {row["external_id"] for row in rows}

    async def has_any_event_sends_for_domme(self, *, domme_user_id: int) -> bool:
        async with self.connection.execute(
            "SELECT 1 FROM event_sends WHERE domme_user_id = ? LIMIT 1",
            (domme_user_id,),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def save_bot_config_ids(self, **kwargs: int) -> None:
        for key, value in kwargs.items():
            if key not in _CONFIG_INT_KEYS:
                continue
            if not value:
                continue
            async with self.connection.execute(
                """
                INSERT INTO bot_config (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, str(value)),
            ):
                pass
        await self.connection.commit()

    async def get_bot_config_ids(self) -> dict[str, int]:
        async with self.connection.execute(
            "SELECT key, value FROM bot_config WHERE key IN ({})".format(
                ",".join("?" * len(_CONFIG_INT_KEYS))
            ),
            tuple(_CONFIG_INT_KEYS),
        ) as cursor:
            rows = await cursor.fetchall()
        result: dict[str, int] = {}
        for row in rows:
            try:
                result[row["key"]] = int(row["value"])
            except (ValueError, TypeError):
                pass
        return result

    async def get_bot_config_values(self, *, keys: list[str]) -> dict[str, str]:
        if not keys:
            return {}
        async with self.connection.execute(
            "SELECT key, value FROM bot_config WHERE key IN ({})".format(
                ",".join("?" * len(keys))
            ),
            tuple(keys),
        ) as cursor:
            rows = await cursor.fetchall()
        return {str(row["key"]): str(row["value"]) for row in rows}

    async def set_bot_config_values(
        self, *, values: dict[str, str | int | None]
    ) -> None:
        for key, value in values.items():
            if value is None:
                await self.connection.execute(
                    "DELETE FROM bot_config WHERE key = ?", (key,)
                )
                continue
            await self.connection.execute(
                """
                INSERT INTO bot_config (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, str(value)),
            )
        await self.connection.commit()

    @staticmethod
    def _event_filter(event_key: str | None) -> tuple[str, tuple[object, ...]]:
        if event_key is None:
            return "", ()
        return "AND event_key = ?", (event_key,)

    # -------------------------------------------------------------------------
    # throne_creators CRUD
    # -------------------------------------------------------------------------

    async def upsert_throne_creator(
        self,
        *,
        guild_id: str,
        discord_user_id: str,
        throne_handle: str,
        throne_creator_id: str,
        hide_own_purchases: bool | None,
        tracking_mode: str,
        webhook_secret: str,
        overlay_detected: bool,
        last_overlay_check_at: str | None = None,
    ) -> ThroneCreator:
        now = _utc_now()
        async with self.connection.execute(
            """
            INSERT INTO throne_creators (
                guild_id,
                discord_user_id,
                throne_handle,
                throne_creator_id,
                hide_own_purchases,
                tracking_mode,
                webhook_secret,
                overlay_detected,
                last_overlay_check_at,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, throne_handle) DO UPDATE SET
                discord_user_id = excluded.discord_user_id,
                throne_creator_id = excluded.throne_creator_id,
                hide_own_purchases = excluded.hide_own_purchases,
                tracking_mode = CASE
                    WHEN throne_creators.tracking_mode = 'webhook' THEN 'webhook'
                    ELSE excluded.tracking_mode
                END,
                webhook_secret = CASE
                    WHEN throne_creators.webhook_secret IS NOT NULL AND throne_creators.webhook_secret != '' THEN throne_creators.webhook_secret
                    ELSE excluded.webhook_secret
                END,
                overlay_detected = excluded.overlay_detected,
                last_overlay_check_at = excluded.last_overlay_check_at,
                updated_at = excluded.updated_at
            """,
            (
                guild_id,
                discord_user_id,
                throne_handle,
                throne_creator_id,
                int(bool(hide_own_purchases))
                if hide_own_purchases is not None
                else None,
                tracking_mode,
                webhook_secret,
                int(bool(overlay_detected)),
                last_overlay_check_at,
                now,
                now,
            ),
        ):
            pass
        await self.connection.commit()
        row = await self.get_throne_creator_by_handle(
            guild_id=guild_id, throne_handle=throne_handle
        )
        if row is None:
            raise RuntimeError(f"Failed to upsert throne_creator for {throne_handle!r}")
        return row

    async def get_throne_creator_by_handle(
        self, *, guild_id: str, throne_handle: str
    ) -> ThroneCreator | None:
        async with self.connection.execute(
            """
            SELECT * FROM throne_creators
            WHERE guild_id = ? AND throne_handle = ?
            """,
            (guild_id, throne_handle),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return ThroneCreator.from_row(row)

    async def get_throne_creators_by_creator_id(
        self, *, throne_creator_id: str
    ) -> list[ThroneCreator]:
        async with self.connection.execute(
            """
            SELECT * FROM throne_creators
            WHERE throne_creator_id = ?
            """,
            (throne_creator_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [ThroneCreator.from_row(r) for r in rows]

    async def get_throne_creators_for_guild(
        self, *, guild_id: str
    ) -> list[ThroneCreator]:
        async with self.connection.execute(
            """
            SELECT * FROM throne_creators
            WHERE guild_id = ?
            ORDER BY created_at ASC
            """,
            (guild_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [ThroneCreator.from_row(r) for r in rows]

    async def replace_throne_wishlist_items(
        self,
        *,
        creator_id: str,
        items: list[ThroneWishlistItem],
    ) -> None:
        await self.connection.executemany(
            """
            INSERT INTO throne_wishlist_items (
                creator_id,
                wishlist_item_id,
                item_name,
                item_image_url,
                amount_usd,
                currency,
                is_available,
                last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(creator_id, wishlist_item_id) DO UPDATE SET
                item_name = excluded.item_name,
                item_image_url = excluded.item_image_url,
                amount_usd = excluded.amount_usd,
                currency = excluded.currency,
                is_available = excluded.is_available,
                last_seen_at = excluded.last_seen_at
            """,
            [
                (
                    creator_id,
                    item.wishlist_item_id,
                    item.item_name,
                    item.item_image_url,
                    item.amount_usd,
                    item.currency,
                    int(bool(item.is_available))
                    if item.is_available is not None
                    else None,
                    item.last_seen_at,
                )
                for item in items
            ],
        )

        if items:
            placeholders = ",".join("?" for _ in items)
            await self.connection.execute(
                f"""
                DELETE FROM throne_wishlist_items
                WHERE creator_id = ?
                AND wishlist_item_id NOT IN ({placeholders})
                """,
                (creator_id, *(item.wishlist_item_id for item in items)),
            )
        else:
            await self.connection.execute(
                "DELETE FROM throne_wishlist_items WHERE creator_id = ?",
                (creator_id,),
            )

        await self.connection.commit()

    async def get_throne_wishlist_items(
        self, *, creator_id: str
    ) -> list[ThroneWishlistItem]:
        async with self.connection.execute(
            """
            SELECT creator_id, wishlist_item_id, item_name, item_image_url, amount_usd, currency, is_available, last_seen_at
            FROM throne_wishlist_items
            WHERE creator_id = ?
            ORDER BY item_name COLLATE NOCASE ASC, wishlist_item_id ASC
            """,
            (creator_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [ThroneWishlistItem.from_row(row) for row in rows]

    async def get_throne_creator_by_discord_user(
        self, *, guild_id: str, discord_user_id: str
    ) -> ThroneCreator | None:
        async with self.connection.execute(
            """
            SELECT * FROM throne_creators
            WHERE guild_id = ? AND discord_user_id = ?
            LIMIT 1
            """,
            (guild_id, discord_user_id),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return ThroneCreator.from_row(row)

    async def get_latest_webhook_send_for_guild(
        self, *, guild_id: str
    ) -> ThroneWebhookSend | None:
        async with self.connection.execute(
            """
            SELECT
                tc.discord_user_id AS discord_user_id,
                es.sub_name AS sub_name,
                es.amount_usd AS amount_usd,
                es.is_private AS is_private,
                es.sent_at AS sent_at
            FROM event_sends es
            INNER JOIN throne_creators tc
                ON tc.discord_user_id = CAST(es.domme_user_id AS TEXT)
            WHERE tc.guild_id = ? AND es.source = 'webhook'
            ORDER BY es.sent_at DESC
            LIMIT 1
            """,
            (guild_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return ThroneWebhookSend(
            discord_user_id=str(row["discord_user_id"]),
            sub_name=row["sub_name"],
            amount_usd=float(row["amount_usd"] or 0.0),
            is_private=bool(row["is_private"]),
            sent_at=str(row["sent_at"]),
        )

    async def get_latest_webhook_send_for_domme(
        self, *, domme_user_id: int
    ) -> ThroneWebhookSend | None:
        async with self.connection.execute(
            """
            SELECT
                CAST(domme_user_id AS TEXT) AS discord_user_id,
                sub_name,
                amount_usd,
                is_private,
                sent_at
            FROM event_sends
            WHERE domme_user_id = ? AND source = 'webhook'
            ORDER BY sent_at DESC
            LIMIT 1
            """,
            (domme_user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return ThroneWebhookSend(
            discord_user_id=str(row["discord_user_id"]),
            sub_name=row["sub_name"],
            amount_usd=float(row["amount_usd"] or 0.0),
            is_private=bool(row["is_private"]),
            sent_at=str(row["sent_at"]),
        )

    async def reset_throne_creator_webhook(
        self,
        *,
        creator_id: int,
        webhook_secret: str,
    ) -> None:
        now = _utc_now()
        await self.connection.execute(
            """
            UPDATE throne_creators
            SET
                webhook_secret = ?,
                tracking_mode = 'disabled',
                webhook_connected_at = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (webhook_secret, now, creator_id),
        )
        await self.connection.commit()

    async def update_throne_creator_webhook_connected(
        self,
        *,
        creator_id: int,
        webhook_connected_at: str,
        last_successful_event_at: str,
    ) -> None:
        now = _utc_now()
        await self.connection.execute(
            """
            UPDATE throne_creators
            SET
                tracking_mode = 'webhook',
                webhook_connected_at = COALESCE(webhook_connected_at, ?),
                last_successful_event_at = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (webhook_connected_at, last_successful_event_at, now, creator_id),
        )
        await self.connection.commit()

    async def add_to_blacklist(
        self,
        *,
        discord_user_id: str,
        reason: str | None = None,
        created_by: str | None = None,
    ) -> None:
        await self.connection.execute(
            """
            INSERT INTO rob_blacklist (discord_user_id, reason, created_at, created_by)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(discord_user_id) DO UPDATE SET
                reason = excluded.reason,
                created_by = excluded.created_by
            """,
            (str(discord_user_id), reason, _utc_now(), created_by),
        )
        await self.connection.commit()

    async def remove_from_blacklist(self, *, discord_user_id: str) -> None:
        await self.connection.execute(
            "DELETE FROM rob_blacklist WHERE discord_user_id = ?",
            (str(discord_user_id),),
        )
        await self.connection.commit()

    async def is_user_blacklisted(self, *, discord_user_id: str) -> bool:
        async with self.connection.execute(
            "SELECT 1 FROM rob_blacklist WHERE discord_user_id = ? LIMIT 1",
            (str(discord_user_id),),
        ) as cursor:
            return (await cursor.fetchone()) is not None

    async def remove_throne_creator_by_discord_user(
        self,
        *,
        guild_id: str,
        discord_user_id: str,
    ) -> str | None:
        """Remove a user's Throne registration. Keeps event_sends history."""
        creator = await self.get_throne_creator_by_discord_user(
            guild_id=guild_id, discord_user_id=str(discord_user_id)
        )
        if creator is None:
            return None
        await self.connection.execute(
            "DELETE FROM throne_wishlist_items WHERE creator_id = ?",
            (creator.throne_creator_id,),
        )
        await self.connection.execute(
            "DELETE FROM throne_creators WHERE id = ?",
            (creator.id,),
        )
        await self.connection.execute(
            "DELETE FROM event_dommes WHERE user_id = ?",
            (int(discord_user_id),),
        )
        await self.connection.commit()
        return creator.throne_creator_id

    async def create_send_request(
        self,
        *,
        sub_user_id: int,
        domme_user_id: int,
        amount_usd: float,
        method: str,
        note: str | None,
        created_at: str | None = None,
    ) -> int:
        async with self.connection.execute(
            """
            INSERT INTO send_requests (
                sub_user_id,
                domme_user_id,
                amount_usd,
                method,
                note,
                status,
                created_at,
                resolved_at
            )
            VALUES (?, ?, ?, ?, ?, 'pending', ?, NULL)
            """,
            (
                sub_user_id,
                domme_user_id,
                amount_usd,
                method,
                note,
                created_at or _utc_now(),
            ),
        ) as cursor:
            request_id = int(cursor.lastrowid)
        await self.connection.commit()
        return request_id

    async def count_send_requests_since(
        self,
        *,
        sub_user_id: int,
        domme_user_id: int,
        since: str,
    ) -> int:
        async with self.connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM send_requests
            WHERE sub_user_id = ?
              AND domme_user_id = ?
              AND created_at >= ?
            """,
            (sub_user_id, domme_user_id, since),
        ) as cursor:
            row = await cursor.fetchone()
        return int(row["count"]) if row is not None else 0

    async def get_send_request(self, *, request_id: int) -> SendRequest | None:
        async with self.connection.execute(
            "SELECT * FROM send_requests WHERE id = ?",
            (request_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return SendRequest.from_row(row)

    async def resolve_send_request(
        self, *, request_id: int, status: str, resolved_at: str | None = None
    ) -> None:
        await self.connection.execute(
            """
            UPDATE send_requests
            SET status = ?, resolved_at = ?
            WHERE id = ?
            """,
            (status, resolved_at or _utc_now(), request_id),
        )
        await self.connection.commit()

    async def delete_send_request(self, *, request_id: int) -> None:
        await self.connection.execute(
            "DELETE FROM send_requests WHERE id = ?",
            (request_id,),
        )
        await self.connection.commit()
