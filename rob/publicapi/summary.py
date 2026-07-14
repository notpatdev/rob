"""``GET /public/guild-summary`` — aggregate stats for the website home page.

No parameters; scoped to the main guild. Always 200 (empty data → zeros/null),
so the home page renders even before any sends exist.

Response shape (no Discord ids; amounts are integer cents):

    {
      "last_updated": "2026-07-14T12:34:56Z",   # or null
      "total_count": 1234,
      "domme_count": 42,
      "sub_count": 300,
      "totals": [{"currency": "USD", "amount_cents": 1234567, "count": 1200}],
      "top_receivers": [
        {"domme_display_name": "Miss X", "amount_cents": 500000,
         "currency": "USD", "count": 120}
      ]
    }
"""

from __future__ import annotations

from aiohttp import web

from rob.config.guilds import MAIN_GUILD_ID
from rob.database.repositories.public_summary import GuildSummary, PublicSummaryRepository
from rob.publicapi.serialization import iso_z

# The public site only surfaces data for the main guild.
PUBLIC_GUILD_ID = MAIN_GUILD_ID


def build_summary_payload(summary: GuildSummary) -> dict:
    return {
        "last_updated": iso_z(summary.last_updated) if summary.last_updated else None,
        "total_count": summary.total_count,
        "domme_count": summary.domme_count,
        "sub_count": summary.sub_count,
        "totals": [
            {
                "currency": total.currency,
                "amount_cents": total.amount_cents,
                "count": total.count,
            }
            for total in summary.totals
        ],
        "top_receivers": [
            {
                "domme_display_name": receiver.domme_display_name,
                "amount_cents": receiver.amount_cents,
                "currency": receiver.currency,
                "count": receiver.count,
            }
            for receiver in summary.top_receivers
        ],
    }


async def handle_guild_summary(request: web.Request) -> web.Response:
    repository: PublicSummaryRepository = request.app["public_summary_repository"]
    summary = await repository.guild_summary(guild_id=PUBLIC_GUILD_ID)
    return web.json_response(build_summary_payload(summary))
