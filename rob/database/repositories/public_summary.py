"""Read-only aggregate stats for the public home page (``/public/guild-summary``).

SELECT-only, guild-scoped, and leaks no Discord ids. Shares the counted-send
filter and domme-label precedence with :mod:`rob.database.repositories.public_sends`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from rob.database.connection import Database
from rob.database.repositories.public_sends import (
    COUNTED_SEND_FILTER_SQL,
    DOMME_LABEL_SQL,
)


@dataclass(frozen=True)
class CurrencyTotal:
    currency: str
    amount_cents: int
    count: int


@dataclass(frozen=True)
class TopReceiver:
    domme_display_name: str
    amount_cents: int
    currency: str
    count: int


@dataclass(frozen=True)
class GuildSummary:
    last_updated: datetime | None
    total_count: int
    domme_count: int
    sub_count: int
    totals: list[CurrencyTotal]
    top_receivers: list[TopReceiver]


# Per-currency totals plus the newest send timestamp, over counted sends.
_TOTALS_SQL = f"""
    SELECT
        s.currency AS currency,
        COUNT(*) AS count,
        COALESCE(SUM(s.amount_cents), 0) AS amount_cents,
        MAX(s.sent_at) AS last_sent_at
    FROM sends s
    WHERE s.guild_id = $1
      AND {COUNTED_SEND_FILTER_SQL}
    GROUP BY s.currency
    ORDER BY amount_cents DESC
"""

# Top 10 receivers by summed amount, grouped by domme + currency. Only the
# public label is selected — never a Discord id.
_TOP_RECEIVERS_SQL = f"""
    SELECT
        {DOMME_LABEL_SQL} AS domme_display_name,
        s.currency AS currency,
        COALESCE(SUM(s.amount_cents), 0) AS amount_cents,
        COUNT(*) AS count
    FROM sends s
    LEFT JOIN dommes d ON d.id = s.domme_id AND d.guild_id = s.guild_id
    WHERE s.guild_id = $1
      AND {COUNTED_SEND_FILTER_SQL}
    GROUP BY d.id, d.public_display_name, d.throne_handle, s.currency
    ORDER BY amount_cents DESC, domme_display_name ASC
    LIMIT 10
"""

_DOMME_COUNT_SQL = """
    SELECT COUNT(*)
    FROM dommes
    WHERE guild_id = $1
      AND leaderboard_visible = true
      AND profile_status = 'active'
"""

_SUB_COUNT_SQL = """
    SELECT COUNT(*)
    FROM subs
    WHERE guild_id = $1
      AND profile_status = 'active'
"""


class PublicSummaryRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def guild_summary(self, *, guild_id: int) -> GuildSummary:
        async with self.database.acquire() as connection:
            totals_rows = await connection.fetch(_TOTALS_SQL, guild_id)
            top_rows = await connection.fetch(_TOP_RECEIVERS_SQL, guild_id)
            domme_count = await connection.fetchval(_DOMME_COUNT_SQL, guild_id)
            sub_count = await connection.fetchval(_SUB_COUNT_SQL, guild_id)

        totals = [
            CurrencyTotal(
                currency=str(row["currency"]),
                amount_cents=int(row["amount_cents"]),
                count=int(row["count"]),
            )
            for row in totals_rows
        ]
        total_count = sum(total.count for total in totals)
        last_updated = max(
            (row["last_sent_at"] for row in totals_rows),
            default=None,
        )
        top_receivers = [
            TopReceiver(
                domme_display_name=str(row["domme_display_name"]),
                amount_cents=int(row["amount_cents"]),
                currency=str(row["currency"]),
                count=int(row["count"]),
            )
            for row in top_rows
        ]
        return GuildSummary(
            last_updated=last_updated,
            total_count=total_count,
            domme_count=int(domme_count or 0),
            sub_count=int(sub_count or 0),
            totals=totals,
            top_receivers=top_receivers,
        )
