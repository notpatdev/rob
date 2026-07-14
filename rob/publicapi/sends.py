"""``GET /public/sends?username=...`` — a Rob user's counted sends.

Powers the website "grab your data" page. Scoped to the main guild, matched
case-insensitively on ``sub_name``, and limited to counted sends. Returns 404
when nothing matches so the frontend can show a clean "no data" state.

Response shape (all 64-bit ids serialized as strings; no Discord ids):

    {
      "username": "someone",
      "resolved_display_name": "Someone",
      "last_updated": "2026-07-10T00:00:00Z",
      "total_count": 3,
      "totals": [{"currency": "USD", "amount_cents": 12500, "count": 3}],
      "recent": [SendRow, ...],       # newest first, max 5
      "all_sends": [SendRow, ...]     # newest first, every counted send
    }
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from aiohttp import web

from rob.config.guilds import MAIN_GUILD_ID
from rob.database.repositories.public_sends import PublicSendRow, PublicSendsRepository

log = logging.getLogger(__name__)

# The public site only surfaces data for the main guild.
PUBLIC_GUILD_ID = MAIN_GUILD_ID

_RECENT_LIMIT = 5


def _iso_z(value: datetime) -> str:
    """ISO 8601 in UTC with a trailing ``Z`` (e.g. ``2026-07-10T00:00:00Z``)."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _send_row(row: PublicSendRow) -> dict:
    return {
        "public_send_id": str(row.public_send_id),
        "sent_at": _iso_z(row.sent_at),
        "amount_cents": int(row.amount_cents),
        "currency": row.currency,
        "domme_display_name": row.domme_display_name,
        "item_name": row.item_name,
        "sub_name": row.sub_name,
    }


def _totals(rows: list[PublicSendRow]) -> list[dict]:
    """Sum ``amount_cents`` and count per currency, biggest total first."""
    buckets: dict[str, dict] = {}
    for row in rows:
        bucket = buckets.setdefault(
            row.currency, {"currency": row.currency, "amount_cents": 0, "count": 0}
        )
        bucket["amount_cents"] += int(row.amount_cents)
        bucket["count"] += 1
    return sorted(
        buckets.values(),
        key=lambda bucket: (-bucket["amount_cents"], bucket["currency"]),
    )


def build_sends_payload(username: str, rows: list[PublicSendRow]) -> dict:
    """Assemble the response body. ``rows`` must be newest-first and non-empty."""
    newest = rows[0]
    return {
        "username": username,
        # The stored casing of the most recent matching send is the friendliest
        # display name we have without touching any Discord identity.
        "resolved_display_name": newest.sub_name or username,
        "last_updated": _iso_z(newest.sent_at),
        "total_count": len(rows),
        "totals": _totals(rows),
        "recent": [_send_row(row) for row in rows[:_RECENT_LIMIT]],
        "all_sends": [_send_row(row) for row in rows],
    }


async def handle_public_sends(request: web.Request) -> web.Response:
    username = (request.query.get("username") or "").strip()
    if not username:
        return web.json_response(
            {"error": "missing_username", "detail": "Provide a ?username= query parameter."},
            status=400,
        )

    repository: PublicSendsRepository = request.app["public_sends_repository"]
    rows = await repository.counted_sends_for_username(
        guild_id=PUBLIC_GUILD_ID,
        username=username,
    )

    if not rows:
        return web.json_response(
            {"error": "not_found", "detail": "No sends found for that username."},
            status=404,
        )

    log.info(
        "Public sends served username=%r rows=%s",
        username,
        len(rows),
    )
    return web.json_response(build_sends_payload(username, rows))
