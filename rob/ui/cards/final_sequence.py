"""Components V2 cards for Rob's 1 August final sequence.

Two cards, both built fresh per send (link buttons are stateless, so no
persistent-view registration is needed):

* :func:`final_thank_you_card` — the farewell DM that accompanies each user's
  keepsake PDF. A plain card (no accent) reads calmer for a goodbye, mirroring
  the shutdown announcement, and carries the same three link buttons.
* :func:`final_stats_card` — Rob's closing numbers for VIB, posted once to the
  leaderboard channel. Built from the public :class:`GuildSummary` aggregate, so
  it never surfaces a Discord id.

The wording here is owner-editable prose; the tests assert card *structure* (the
numbers are present, buttons point at the configured URLs) rather than exact
copy, so Pat can reword any of it without breaking the build.
"""

from __future__ import annotations

import discord

from rob.database.repositories.public_summary import GuildSummary
from rob.ui.cards.shutdown import FINBOT_URL, PIGEON_URL, ROB_WEBSITE_URL
from rob.ui.render import RenderedMessage
from rob.ui.theme import COLOR_SEND

# The keepsake PDF filename attached to the thank-you DM.
KEEPSAKE_PDF_FILENAME = "your-rob-sends.pdf"

# How many receiver rows the final stats card lists.
_TOP_RECEIVERS_SHOWN = 5

_CURRENCY_SYMBOLS = {"USD": "$", "AUD": "$", "CAD": "$", "EUR": "€", "GBP": "£"}


def _money(amount_cents: int, currency: str) -> str:
    symbol = _CURRENCY_SYMBOLS.get((currency or "").upper())
    value = f"{amount_cents / 100:,.2f}"
    return f"{symbol}{value}" if symbol else f"{value} {currency}"


# --- Thank-you DM ----------------------------------------------------------

_THANK_YOU_TITLE = "## Thank you, from Rob."

_THANK_YOU_BODY = "\n\n".join(
    (
        "This is the last you'll hear from Rob.",
        "Attached is your keepsake — a PDF of every send Rob tracked for you. "
        "It's yours to keep. After today, the website will no longer show your "
        "individual data, and all send data on Rob is being anonymised for "
        "privacy.",
        "Thank you for being part of Rob. It genuinely meant a lot.",
        "The links below still work if you'd like to keep your findom fun going "
        "elsewhere.",
        "Pat",
    )
)


def _link_button(label: str, url: str) -> discord.ui.Button:
    return discord.ui.Button(style=discord.ButtonStyle.link, label=label, url=url)


def _section_break() -> discord.ui.Separator:
    return discord.ui.Separator(spacing=discord.SeparatorSpacing.large)


class FinalThankYouView(discord.ui.LayoutView):
    """The farewell DM: heading, body, and the three link buttons. No accent
    colour, matching the shutdown announcement."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        container = discord.ui.Container()  # no accent_color: plain card
        container.add_item(discord.ui.TextDisplay(_THANK_YOU_TITLE))
        container.add_item(_section_break())
        container.add_item(discord.ui.TextDisplay(_THANK_YOU_BODY))
        container.add_item(_section_break())
        container.add_item(
            discord.ui.ActionRow(
                _link_button("FinBot", FINBOT_URL),
                _link_button("Rob Website", ROB_WEBSITE_URL),
                _link_button("Pigeon", PIGEON_URL),
            )
        )
        self.add_item(container)


def final_thank_you_card() -> RenderedMessage:
    """The farewell DM sent alongside each user's keepsake PDF."""
    return RenderedMessage(view=FinalThankYouView())


# --- Final VIB stats -------------------------------------------------------


def _totals_lines(summary: GuildSummary) -> str:
    if not summary.totals:
        return "No sends were counted."
    return "\n".join(
        f"**{_money(total.amount_cents, total.currency)}** "
        f"across **{total.count}** send(s)"
        for total in summary.totals
    )


def _top_receivers_lines(summary: GuildSummary) -> str:
    if not summary.top_receivers:
        return "No receivers to list."
    lines = []
    for rank, receiver in enumerate(summary.top_receivers[:_TOP_RECEIVERS_SHOWN], 1):
        lines.append(
            f"**{rank}.** {receiver.domme_display_name} — "
            f"{_money(receiver.amount_cents, receiver.currency)} "
            f"({receiver.count} send(s))"
        )
    return "\n".join(lines)


def _for_a_laugh_lines(summary: GuildSummary) -> str:
    """A couple of light, aggregate-only stats. Owner-editable — swap in
    funnier hidden stats here whenever you like."""
    lines = [f"That's **{summary.total_count}** send(s) in total. Not bad, VIB."]
    if summary.totals:
        biggest = summary.totals[0]
        if biggest.count:
            average = biggest.amount_cents // biggest.count
            lines.append(
                f"Average {biggest.currency} send: "
                f"**{_money(average, biggest.currency)}**."
            )
    return "\n".join(lines)


class FinalStatsView(discord.ui.LayoutView):
    """Rob's closing numbers for VIB, posted to the leaderboard channel."""

    def __init__(self, summary: GuildSummary) -> None:
        super().__init__(timeout=None)
        container = discord.ui.Container(accent_color=COLOR_SEND)
        container.add_item(discord.ui.TextDisplay("-# VIB · final stats"))
        container.add_item(discord.ui.TextDisplay("## Rob's final numbers"))
        container.add_item(
            discord.ui.TextDisplay(
                "Before the lights go out, here's what Rob tracked for VIB."
            )
        )
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(f"**Totals**\n{_totals_lines(summary)}")
        )
        container.add_item(
            discord.ui.TextDisplay(
                "**The people**\n"
                f"**{summary.domme_count}** registered Dom/me(s)\n"
                f"**{summary.sub_count}** registered Sub(s)"
            )
        )
        container.add_item(
            discord.ui.TextDisplay(
                f"**Top receivers**\n{_top_receivers_lines(summary)}"
            )
        )
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(f"**For a laugh**\n{_for_a_laugh_lines(summary)}")
        )
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.TextDisplay(
                "-# Thanks for being part of Rob. — Pat"
            )
        )
        self.add_item(container)


def final_stats_card(summary: GuildSummary) -> RenderedMessage:
    """Rob's final VIB stats, posted once to the leaderboard channel."""
    return RenderedMessage(view=FinalStatsView(summary))
