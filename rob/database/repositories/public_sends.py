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


# The domme label mirrors the leaderboard precedence: curated public name, then
# the public Throne handle, then a generic fallback. Never the Discord id.
_DOMME_LABEL_SQL = """
    COALESCE(
        NULLIF(TRIM(d.public_display_name), ''),
        NULLIF(TRIM(d.throne_handle), ''),
        'A Dom/me'
    )
"""

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
        {_DOMME_LABEL_SQL} AS domme_display_name
    FROM sends s
    LEFT JOIN dommes d ON d.id = s.domme_id
    WHERE s.guild_id = $1
      AND s.discord_post_status = 'posted'
      AND s.is_private = false
      AND s.is_test_send IS NOT TRUE
      AND lower(s.sub_name) = lower($2)
    ORDER BY s.sent_at DESC, s.id DESC
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
        """Every counted send whose ``sub_name`` matches ``username`` (ci).

        Returned newest first. Empty list when nothing matches.
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
