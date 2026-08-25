from __future__ import annotations

import discord

from rob.ui.render import CardAction, CardSection, RobCard, RenderedMessage, render_card
from rob.ui.theme import COLOR_INFO


def make_card(*, title: str, body: str, color: discord.Colour | None = None, footer: str | None = None, sections: list[CardSection] | None = None, image_url: str | None = None, actions: list[CardAction] | None = None, variant: str = "default", eyebrow: str | None = None, callout: str | None = None, code_block: str | None = None) -> RobCard:
    return RobCard(title=title, body=body, color=color or COLOR_INFO, footer=footer, sections=sections or [], image_url=image_url, actions=actions or [], variant=variant, eyebrow=eyebrow, callout=callout, code_block=code_block)


def render(card: RobCard, *, view: discord.ui.LayoutView | None = None) -> RenderedMessage:
    return render_card(card, view=view)
