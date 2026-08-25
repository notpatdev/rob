from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

import discord

_REQUIRED_V2 = ("LayoutView", "Container", "Section", "TextDisplay", "Separator", "MediaGallery", "Thumbnail", "Button")
CardVariant = Literal["default", "success", "error", "warning", "setup", "leaderboard", "send", "counting", "status"]


@dataclass(frozen=True)
class CardSection:
    title: str
    text: str
    inline: bool = False


@dataclass(frozen=True)
class CardAction:
    label: str
    style: discord.ButtonStyle = discord.ButtonStyle.secondary
    custom_id: str | None = None
    url: str | None = None
    row: int | None = None


@dataclass(frozen=True)
class RobCard:
    title: str
    body: str
    sections: list[CardSection] = field(default_factory=list)
    footer: str | None = None
    image_url: str | None = None
    actions: list[CardAction] = field(default_factory=list)
    color: discord.Colour | None = None
    variant: CardVariant = "default"
    eyebrow: str | None = None
    callout: str | None = None
    code_block: str | None = None


@dataclass(frozen=True)
class RenderedMessage:
    content: str | None = None
    view: discord.ui.View | discord.ui.LayoutView | None = None
    embeds: list[discord.Embed] = field(default_factory=list)
    mode: Literal["components_v2", "embed"] = "components_v2"

    def send_kwargs(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if self.content is not None:
            payload["content"] = self.content
        if self.embeds:
            payload["embeds"] = list(self.embeds)
        if self.view is not None:
            payload["view"] = self.view
        return payload

    def edit_kwargs(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "embeds": list(self.embeds),
            "attachments": [],
            "view": self.view,
        }


def supports_components_v2() -> bool:
    return all(hasattr(discord.ui, name) for name in _REQUIRED_V2)


def require_components_v2() -> None:
    if supports_components_v2():
        return
    missing = [name for name in _REQUIRED_V2 if not hasattr(discord.ui, name)]
    raise RuntimeError(f"Discord Components V2 is required for Rob card rendering. Missing: {', '.join(missing)}")


def build_action_row(*buttons: discord.ui.Button) -> discord.ui.ActionRow:
    if not hasattr(discord.ui, "ActionRow"):
        raise RuntimeError("discord.ui.ActionRow is required to place buttons in Components V2 layouts.")
    return discord.ui.ActionRow(*buttons)


def add_card_actions(view: discord.ui.LayoutView, *buttons: discord.ui.Button) -> None:
    if not buttons:
        return
    view.add_item(build_action_row(*buttons))


def render_card(card: RobCard, *, view: discord.ui.LayoutView | None = None) -> RenderedMessage:
    require_components_v2()
    if view is not None and len(view.children) > 0:
        raise RuntimeError("render_card(view=...) expects an empty LayoutView.")
    layout = view or discord.ui.LayoutView(timeout=1800)
    items: list[Any] = []
    if card.eyebrow:
        items.append(discord.ui.TextDisplay(f"-# {card.eyebrow}"))
    items.append(discord.ui.TextDisplay(f"## {card.title}"))
    items.append(discord.ui.TextDisplay(card.body))
    if card.callout:
        items.append(discord.ui.Separator())
        items.append(discord.ui.TextDisplay(card.callout))
    if card.code_block:
        items.append(discord.ui.TextDisplay(f"```\n{card.code_block}\n```"))
    if card.sections:
        items.append(discord.ui.Separator())
        for section in card.sections:
            items.append(discord.ui.TextDisplay(f"**{section.title}**\n{section.text}"))
    if card.image_url:
        items.append(discord.ui.Separator())
        items.append(discord.ui.MediaGallery(discord.MediaGalleryItem(media=card.image_url)))
    if card.footer:
        items.append(discord.ui.Separator())
        items.append(discord.ui.TextDisplay(f"-# {card.footer}"))
    layout.add_item(discord.ui.Container(*items, accent_color=card.color))
    return RenderedMessage(view=layout)
