from __future__ import annotations

import discord

from rob.ui.components import make_card, render
from rob.ui.copy import DOMME_REGISTERED_BODY, DOMME_REGISTERED_TITLE, THRONE_SETUP_TITLE
from rob.ui.render import CardSection, RenderedMessage
from rob.ui.theme import COLOR_INFO, COLOR_SUCCESS


def registration_card(
    *,
    title: str,
    summary: str,
    details: list[tuple[str, str]] | None = None,
    footer: str | None = None,
    view: discord.ui.LayoutView | None = None,
) -> RenderedMessage:
    sections = [CardSection(title=name, text=value) for name, value in (details or [])]
    return render(
        make_card(
            title=title,
            body=summary,
            color=COLOR_SUCCESS,
            footer=footer,
            sections=sections,
            variant="success",
        ),
        view=view,
    )


def domme_registered_card(
    *,
    footer: str | None = None,
    view: discord.ui.LayoutView | None = None,
) -> RenderedMessage:
    return render(
        make_card(
            title=DOMME_REGISTERED_TITLE,
            body=DOMME_REGISTERED_BODY,
            color=COLOR_SUCCESS,
            footer=footer,
            variant="success",
        ),
        view=view,
    )


def throne_setup_card(
    description: str,
    *,
    title: str = THRONE_SETUP_TITLE,
    image_url: str | None = None,
    view: discord.ui.LayoutView | None = None,
) -> RenderedMessage:
    return render(
        make_card(
            title=title,
            body=description,
            color=COLOR_INFO,
            image_url=image_url,
            variant="setup",
            eyebrow="Setup",
        ),
        view=view,
    )
