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

_TITLE = "## Rob is saying Goodbye, for now..."

# Intro paragraphs, joined with blank lines for clear spacing.
_INTRO = "\n\n".join(
    (
        "Hello!",
        "After nearly 3 awesome months of Rob being active, tracking sends, "
        "counting the count and tracking inactivity peeps, I have to announce "
        "that I will now be taking Rob offline.",
        "He has become an important part of VIB, helping Dom/me's and Subs track "
        "their sends in a fast and efficient fashion. And while I don't want to "
        "disable Rob, I feel I have to as I have been facing a big decline in my "
        "mental health over the last few weeks.",
        "The timeline of events are as follows:",
    )
)

# Dated timeline. Date labels are bolded so each step stands out.
_TIMELINE = "\n\n".join(
    (
        "**Now:** This announcement, as well as the ability to visit the Rob "
        "website and download a record of all of your sends (for both Dom/me's "
        "and Subs).",
        "**16th of July at 8am (AEST):** Rob's core features such as Send "
        "Tracking, Send Leaderboard, Count Tracking, inactivity, etc. will be "
        "turned off. Rob's backend will continue to track any sends made on "
        "Throne during this period and manual send addition will continue to "
        "work.",
        "**20th of July at 8am (AEST):** Rob will cease to track sends and all "
        "webhook URL's provided to Dom/me's will be made invalid.",
        "**1st of August at 8am (AEST):** Any further systems still active on Rob "
        "will be turned off and Rob will officially go offline. This will also "
        "become the final day to view or download a copy of any and all sends "
        "tracked for you by Rob. After that, for your privacy, all individual "
        "send data will be permanently anonymised — the Rob website will then "
        "display only the final total amounts and allow you all to download a "
        "copy of the final server-totalled data.",
    )
)

# Closing paragraphs.
_CLOSING = "\n\n".join(
    (
        "Allowing you to pull your own send data means you can, if you wish, use "
        "FinBot — which has been around longer than Rob — to manually track your "
        "sends.",
        "Below are 3 links: 1. the link to FinBot (should you wish to use it), "
        "2. the link to the Rob website, 3. the link to the new Pigeon bot "
        "website.",
        "I will take time off Discord and check in every now and then, and in a "
        "month's time I will begin planning to build the Pigeon bot, with a hope "
        "to have it online by the end of the year.",
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
