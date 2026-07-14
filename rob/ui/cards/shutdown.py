"""Components V2 cards for Rob's shutdown announcement (``/shutdown``).

The announcement is a farewell DM sent to Rob users. Three stateless link
buttons point at FinBot, the "grab your data" page, and the new Pigeon site.
Link buttons carry no ``custom_id`` and need no callback or persistent-view
registration, so this card can be built fresh per recipient and sent as-is.
"""

from __future__ import annotations

import discord

from rob.ui.render import RenderedMessage
from rob.ui.theme import COLOR_INFO, COLOR_SUCCESS

# Destinations for the three link buttons on the announcement.
FINBOT_URL = "https://www.thefinbot.xyz/"
GRAB_DATA_URL = "https://www.robthebot.com/sends/"
PIGEON_URL = "https://pigeonbot.xyz"

_TITLE = "# Goodbye, for now..."

# Each entry is one paragraph/list block; joined with blank lines so paragraphs
# read with clear spacing while the numbered list stays tight.
_BODY = "\n\n".join(
    (
        "Hello!",
        "After an awesome nearly 3 months of Rob being active, tracking sends, "
        "tracking the count and so on, Rob will now be shutting down.",
        "I am shutting Rob down for now as my mental health is deteriorating and "
        "I'm needing to take a break from everything for a bit. I will in the "
        "background be planning the new bot **Pigeon** and will start work on it "
        "in roughly a month depending on how I am going.",
        "This wasn't an easy decision to make as I know how you guys find Rob "
        "really handy for tracking sends.",
        "To make this easier, I have some links in the buttons below.",
        "1. **FinBot** - It's a well known Findom Bot that allows you to manually "
        "track sends.",
        "2. **Download your Data** - To help you quickly get your sends onto "
        "FinBot if you choose to use it. You can download a record of all your "
        "sends including the total amount you received and who they were from, "
        "this will be available to all Dom/mes and Subs (where possible). To grab "
        "your data, simply click the button below and enter your Throne username.",
        "3. **Pigeon** - A link to the new Pigeon website (still under "
        "construction but it will be where I make announcements around the new "
        "bot).",
        "Thank you for helping to make Rob into the bot he is today.",
    )
)

_FOOTER = (
    "-# Rob's main features will shutdown on the 15th of July at 8am (AEST) and "
    "his send tracking systems will continue to run until the 18th of July at "
    "8am (AEST). You will be able to obtain a copy of your sends from the website "
    "until the end of the month, 2nd of August at 8am (AEST), where the Rob "
    "website will then display his final stats until I decide to shut it down."
)


def _link_button(label: str, url: str) -> discord.ui.Button:
    return discord.ui.Button(style=discord.ButtonStyle.link, label=label, url=url)


class ShutdownAnnouncementView(discord.ui.LayoutView):
    """The farewell card: heading, body, small-print timing note, link buttons."""

    def __init__(self) -> None:
        # Link buttons are stateless, so the view never needs to time out or
        # re-bind; timeout=None keeps the buttons alive indefinitely.
        super().__init__(timeout=None)
        container = discord.ui.Container(accent_color=COLOR_INFO)
        container.add_item(discord.ui.TextDisplay(_TITLE))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(_BODY))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(_FOOTER))
        container.add_item(discord.ui.Separator())
        container.add_item(
            discord.ui.ActionRow(
                _link_button("FinBot", FINBOT_URL),
                _link_button("Grab your data", GRAB_DATA_URL),
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
