"""Read-only query for the public "grab your data" endpoint.

This repository is deliberately SELECT-only and returns a lean row shape that
carries no Discord ids. The public API service connects with a SELECT-only
database role, but this module never issues writes regardless, so it is safe to
run against any pool.

A "counted send" (the same definition the leaderboard and public site use) is:

* ``discord_post_status = 'posted'``
* ``is_private = false``
* ``is_test_send`` is not true

Rows are matched case-insensitively on ``sends.sub_name`` and scoped to a single
guild, newest first.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from rob.database.connection import Database
from rob.utils.send_ids import generate_public_send_id


@dataclass(frozen=True)
class PublicSendRow:
    """One counted send, shaped for public serialization.

    Only fields safe to expose publicly live here — no numeric send id, no
    ``sub_user_id``/``domme_user_id`` or any other Discord id.
    """

    public_send_id: str
    sent_at: datetime
    amount_cents: int
    currency: str
    domme_display_name: str
    item_name: str | None
    sub_name: str | None


# Shared by both public endpoints. A "counted send" is posted, not private, and
# not a test send. Interpolated as a WHERE fragment on alias ``s``.
COUNTED_SEND_FILTER_SQL = (
    "s.discord_post_status = 'posted' "
    "AND s.is_private = false "
    "AND s.is_test_send IS NOT TRUE"
)

# The domme label mirrors the leaderboard precedence: curated public name, then
# the public Throne handle, then a generic fallback. Never the Discord id.
# Expects alias ``d`` (LEFT JOIN dommes d ON d.id = s.domme_id AND ...).
DOMME_LABEL_SQL = (
    "COALESCE("
    "NULLIF(TRIM(d.public_display_name), ''), "
    "NULLIF(TRIM(d.throne_handle), ''), "
    "'Registered Dom/me')"
)

# A username matches either the sub who *sent* (``sub_name``) or the Dom/me who
# *received* (their ``throne_handle``). Throne usernames are globally unique, so
# a name resolves to one person even across the two roles — a Dom/me who also
# sends sees both. This is what lets Dom/mes (recipients) pull their data, not
# just subs (senders).
_USERNAME_MATCH_SQL = """(
        lower(s.sub_name) = lower($2)
        OR s.domme_id IN (
            SELECT d2.id
            FROM dommes d2
            WHERE d2.guild_id = $1
              AND lower(d2.throne_handle) = lower($2)
        )
    )"""

# ``domme_user_id`` and the raw send id are selected only to reconstruct a
# stable ``public_send_id`` for the rare legacy row where the column is still
# NULL. They are used to derive the public id and are never serialized out.
_COUNTED_SENDS_SQL = f"""
    SELECT
        s.id AS id,
        s.guild_id AS guild_id,
        s.public_send_id AS public_send_id,
        s.event_id AS event_id,
        s.fallback_event_hash AS fallback_event_hash,
        s.created_at AS created_at,
        s.domme_user_id AS domme_user_id,
        s.sent_at AS sent_at,
        s.amount_cents AS amount_cents,
        s.currency AS currency,
        s.item_name AS item_name,
        s.sub_name AS sub_name,
        {DOMME_LABEL_SQL} AS domme_display_name
    FROM sends s
    LEFT JOIN dommes d ON d.id = s.domme_id AND d.guild_id = s.guild_id
    WHERE s.guild_id = $1
      AND {COUNTED_SEND_FILTER_SQL}
      AND {_USERNAME_MATCH_SQL}
    ORDER BY s.sent_at DESC, s.id DESC
"""

# Resolve the Dom/me's public label for ``resolved_display_name`` when the query
# matched a recipient rather than a sender.
_DOMME_LABEL_FOR_USERNAME_SQL = f"""
    SELECT {DOMME_LABEL_SQL} AS label
    FROM dommes d
    WHERE d.guild_id = $1
      AND lower(d.throne_handle) = lower($2)
    ORDER BY d.id
    LIMIT 1
"""


def _resolve_public_send_id(row) -> str:
    """Return the stored public send id, or a deterministic fallback.

    Mirrors :func:`rob.utils.send_ids.build_public_send_id` so a fallback id
    computed here matches the value the bot would persist for the same row.
    """

    stored = row["public_send_id"]
    if stored:
        return str(stored)
    stable_seed = (
        row["event_id"]
        or row["fallback_event_hash"]
        or (
            f"{row['created_at'].isoformat()}:{row['domme_user_id']}:"
            f"{row['amount_cents']}:{row['sent_at'].isoformat()}"
        )
    )
    return generate_public_send_id(
        int(row["id"]),
        guild_id=int(row["guild_id"]),
        stable_seed=stable_seed,
    )


class PublicSendsRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    async def counted_sends_for_username(
        self,
        *,
        guild_id: int,
        username: str,
    ) -> list[PublicSendRow]:
        """Every counted send for ``username`` (ci), newest first.

        Matches the sub who sent (``sub_name``) or the Dom/me who received
        (``dommes.throne_handle``). Empty list when nothing matches.
        """

        async with self.database.acquire() as connection:
            rows = await connection.fetch(_COUNTED_SENDS_SQL, guild_id, username)
        return [
            PublicSendRow(
                public_send_id=_resolve_public_send_id(row),
                sent_at=row["sent_at"],
                amount_cents=int(row["amount_cents"]),
                currency=str(row["currency"]),
                domme_display_name=str(row["domme_display_name"]),
                item_name=row["item_name"],
                sub_name=row["sub_name"],
            )
            for row in rows
        ]

    async def domme_label_for_username(
        self,
        *,
        guild_id: int,
        username: str,
    ) -> str | None:
        """The Dom/me's public label when ``username`` is a registered Dom/me
        handle, else None. Used to set ``resolved_display_name`` for recipients
        (whose ``sub_name`` on each row is the *sender*, not them)."""

        async with self.database.acquire() as connection:
            row = await connection.fetchrow(
                _DOMME_LABEL_FOR_USERNAME_SQL, guild_id, username
            )
        return str(row["label"]) if row is not None else None
