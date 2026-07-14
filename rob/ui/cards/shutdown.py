"""Components V2 cards for Rob's shutdown announcement (``/shutdown``).

The announcement is a farewell DM sent to Rob users. Three stateless link
buttons point at FinBot, the Rob website, and the new Pigeon site. Link buttons
carry no ``custom_id`` and need no callback or persistent-view registration, so
this card can be built fresh per recipient and sent as-is.

The announcement container has no accent colour — a plain card reads calmer for
a goodbye; large separators give each section room to breathe.
"""

from __future__ import annotations

import discord

from rob.ui.render import RenderedMessage
from rob.ui.theme import COLOR_INFO, COLOR_SUCCESS

# Destinations for the three link buttons on the announcement.
FINBOT_URL = "https://www.thefinbot.xyz/"
ROB_WEBSITE_URL = "https://www.robthebot.com/"
PIGEON_URL = "https://pigeonbot.xyz"

_TITLE = "## Good afternoon, good evening & goodnight..."

# Intro paragraphs, joined with blank lines for clear spacing.
_INTRO = "\n\n".join(
    (
        "Hello!",
        "Rob has been active for nearly 3 months now, tracking sends, keeping "
        "the Count and keeping an eye on inactive members. In that time he's "
        "become a real fixture in VIB, helping Dom/me's and Subs stay on top of "
        "their sends quickly and reliably.",
        "Which makes this a hard one to write: I've decided to take Rob offline. "
        "My mental health has taken a real hit over the last few weeks, and "
        "stepping back from running Rob is part of what I need to do to look "
        "after it. It isn't a decision I wanted to make, but it's the right one "
        "for me right now.",
        "Here's how the wind-down will work:",
    )
)
# Dated timeline. Date labels are bolded so each step stands out.
_TIMELINE = "\n\n".join(
    (
        "**Now:** This announcement goes out, and the Rob website opens up so you "
        "can download a full record of your sends (for both Dom/me's and Subs).",
        "**16th of July, 8am (AEST):** Rob's core features (Send Tracking, the "
        "Send Leaderboard and inactivity tracking) switch off. Behind the scenes "
        "he'll keep logging any sends made on Throne, manual send addition still "
        "works, and the Count keeps ticking over.",
        "**20th of July, 8am (AEST):** Rob stops tracking sends entirely, and "
        "every webhook URL handed out to Dom/me's is retired.",
        "**1st of August, 8am (AEST):** Everything still running on Rob shuts "
        "down and he officially goes offline. This is your last day to view or "
        "download a copy of everything Rob has tracked for you. After that, all "
        "individual send data is permanently anonymised for your privacy, and "
        "the website will show only the final totals, with the server-totalled "
        "data still available to download.",
    )
)
# Closing paragraphs.
_CLOSING = "\n\n".join(
    (
        "If you'd like to keep tracking your sends by hand from here, you can "
        "pull your data out of Rob and carry it over to FinBot, which has been "
        "around even longer than Rob.",
        "You'll find 3 links below: 1. FinBot, if you'd like to use it, 2. the "
        "Rob website, and 3. the new Pigeon bot website (still a work in "
        "progress).",
        "I'll be keeping a low profile on Discord for a while as I plan things "
        "out and get myself into the right headspace to build Pigeon. I'm hoping "
        "to start in a month or so and have it live by the end of the year.",
        "Thank you for being part of Rob.",
    )
)

def _link_button(label: str, url: str) -> discord.ui.Button:
    return discord.ui.Button(style=discord.ButtonStyle.link, label=label, url=url)


def _section_break() -> discord.ui.Separator:
    return discord.ui.Separator(spacing=discord.SeparatorSpacing.large)


class ShutdownAnnouncementView(discord.ui.LayoutView):
    """The farewell card: heading, intro, timeline, closing, link buttons.

    No accent colour; large separators between each section.
    """

    def __init__(self) -> None:
        # Link buttons are stateless, so the view never needs to time out or
        # re-bind; timeout=None keeps the buttons alive indefinitely.
        super().__init__(timeout=None)
        container = discord.ui.Container()  # no accent_color: plain card
        container.add_item(discord.ui.TextDisplay(_TITLE))
        container.add_item(_section_break())
        container.add_item(discord.ui.TextDisplay(_INTRO))
        container.add_item(_section_break())
        container.add_item(discord.ui.TextDisplay(_TIMELINE))
        container.add_item(_section_break())
        container.add_item(discord.ui.TextDisplay(_CLOSING))
        container.add_item(_section_break())
        container.add_item(
            discord.ui.ActionRow(
                _link_button("FinBot", FINBOT_URL),
                _link_button("Rob Website", ROB_WEBSITE_URL),
                _link_button("Pigeon", PIGEON_URL),
            )
        )
        self.add_item(container)


def shutdown_announcement_card() -> RenderedMessage:
    """The farewell announcement DM'd to Rob users."""
    return RenderedMessage(view=ShutdownAnnouncementView())


class _ShutdownSentLayout(discord.ui.LayoutView):
    def __init__(self, *, sent: int, failed: int) -> None:
        super().__init__(timeout=60)
        color = COLOR_SUCCESS if failed == 0 else COLOR_INFO
        container = discord.ui.Container(accent_color=color)
        container.add_item(discord.ui.TextDisplay("-# Shutdown announcement"))
        container.add_item(discord.ui.TextDisplay("## Announcement sent"))
        summary = f"Delivered the shutdown announcement to **{sent}** recipient(s)."
        if failed:
            summary += f"\n**{failed}** could not be reached (DMs closed or blocked)."
        container.add_item(discord.ui.TextDisplay(summary))
        self.add_item(container)


def shutdown_sent_card(*, sent: int, failed: int) -> RenderedMessage:
    """Ephemeral confirmation shown to the owner after sending."""
    return RenderedMessage(view=_ShutdownSentLayout(sent=sent, failed=failed))
