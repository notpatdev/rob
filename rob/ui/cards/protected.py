from __future__ import annotations

import discord

from rob.ui.components import make_card, render
from rob.ui.render import RenderedMessage
from rob.ui.theme import COLOR_WHITE


def protected_member_card(
    *,
    user_id: int,
    view: discord.ui.LayoutView | None = None,
) -> RenderedMessage:
    """Memorial card announcing that a member's account is protected.

    The sender should pass allowed_mentions so the body's mention does not
    ping the account; view is where the caller attaches the GoFundMe button.
    """
    body = (
        "In loving memory of Aly, "
        f"Rob will always watch over her account (<@{user_id}>) here in VIB and "
        "safeguard it from any change. Her place in this community is permanent."
    )
    footer = (
        "Aly is shielded from the inactivity system and every other automation "
        "that could remove her from the server.\n"
        "-# In every server backup, Aly's account is preserved and prioritised above all else."
    )
    return render(
        make_card(
            title="🪽 Protected Member 🪽",
            body=body,
            color=COLOR_WHITE,
            footer=footer,
            variant="default",
        ),
        view=view,
    )
