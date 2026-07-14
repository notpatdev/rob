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
        "Over the last 3 awesome months, Rob has been there tracking your sends, "
        "keeping an eye on the count and making sure inactive members were dealt "
        "with. He has essentially become a crucial part of everyone's findom fun "
        "making sure you're aware of sends long before Throne even made you aware "
        "of it.",
        "However, today… I announce that Rob's services will soon be coming to an "
        "end. This was not a decision that was easy to make given the reliance on "
        "Rob, however given my mental health has been taking a real hit over the "
        "last few weeks, I feel it's better to step away from Rob and socials for "
        "a bit and focus on myself. This won't be immediate, far from it. But over "
        "the last few weeks I've been attempting to build something to help give "
        "you a keepsake of Rob and help with moving your sends to other bots once "
        "he goes offline.",
        "Here's how the shutdown will work:",
    )
)
# Dated timeline. Date labels are bolded so each step stands out.
_TIMELINE = "\n\n".join(
    (
        "**Right Now:** Nothing, Rob will still continue to run as he has. Rob's "
        "website becomes available for those to view or download send stats.",
        "**16th of July at 8am (AEST):** Some of Rob's core features will be "
        "turned off. These include the send leaderboard and send tracking, the "
        "inactivity system, Rob's warn notification system, etc. Note: Rob will "
        "still continue to track sends made on Throne in the background as well "
        "as Dom/me's will still be able to manually add sends to Rob. And the "
        "count will still continue to run.",
        "**20th of July at 8am (AEST):** Rob's send tracking system will be "
        "completely turned off. Manual sends will no longer be able to be added "
        "and all URLs provided by Rob will be invalidated. This will leave the "
        "count as the only system running.",
        "**1st of August at 8am (AEST):** Rob will finally come to an end. All "
        "users of Rob or anyone with any send data on Rob will receive one final "
        "thank you message along with a PDF (I know, so professional) detailing "
        "all of your sends Rob tracked.\n\nRob will also send his final send "
        "stats to the leaderboard channel outlining all of the stats for VIB as "
        "a whole.",
    )
)
# Closing paragraphs.
_CLOSING = "\n\n".join(
    (
        "As of the 1st of August, once all final messages are sent, the Rob "
        "website will no longer allow you to retrieve or view your own send "
        "stats, instead displaying one simple page showing all of VIB's stats "
        "and some hidden Rob stats that should give you a good laugh. This day "
        "will also mark the day all send data is anonymised for privacy.",
        "Below are a few links which may serve some use. A link to FinBot, the "
        "inspiration to Rob. A link to the Rob website and a link to the new "
        "Pigeon bot which I hope to start planning and building in a month or "
        "so's time.",
        "Over the next few months, I'll be keeping fairly quiet on socials while "
        "I build the mental capacity to come back and build Pigeon.",
        "Thank you for being part of Rob.",
        "Pat",
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
