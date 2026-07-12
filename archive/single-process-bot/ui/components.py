from __future__ import annotations

import logging
from collections.abc import Iterable

import discord

from bot.ui.copy import footer as subtle_footer
from bot.ui.theme import COLOR_INFO

log = logging.getLogger(__name__)


def _coerce_color(
    accent_color: discord.Colour | int | None,
) -> discord.Colour | int | None:
    if accent_color is None:
        return COLOR_INFO
    return (
        discord.Colour(accent_color) if isinstance(accent_color, int) else accent_color
    )


def text_block(text: str) -> discord.ui.TextDisplay:
    return discord.ui.TextDisplay(text)


def subtle(text: str) -> discord.ui.TextDisplay:
    return discord.ui.TextDisplay(subtle_footer(text))


def separator(*, visible: bool = True) -> discord.ui.Separator:
    return discord.ui.Separator(visible=visible)


def action_section(text: str, button: discord.ui.Button) -> discord.ui.Section:
    return discord.ui.Section(text, accessory=button)


def thumbnail_section(text: str, image_url: str) -> discord.ui.Section:
    return discord.ui.Section(text, accessory=discord.ui.Thumbnail(image_url))


def media_gallery(*urls: str) -> discord.ui.MediaGallery | None:
    filtered_urls = [url for url in urls if url]
    try:
        items = [discord.MediaGalleryItem(url) for url in filtered_urls]
    except (TypeError, ValueError):
        log.exception("Ignoring invalid media gallery URL(s): %s", filtered_urls)
        return None
    if not items:
        return None
    return discord.ui.MediaGallery(*items)


def make_container(
    title: str,
    body: str | None = None,
    *,
    sections: Iterable[discord.ui.Item] | None = None,
    footer: str | None = None,
    accent_color: discord.Colour | int | None = None,
) -> discord.ui.Container:
    header = f"## {title}"
    if body:
        header = f"{header}\n\n{body}"

    items: list[discord.ui.Item] = [text_block(header)]
    if sections:
        items.extend(sections)
    if footer:
        items.append(subtle(footer))

    return discord.ui.Container(*items, accent_color=_coerce_color(accent_color))


def replace_container(
    view: discord.ui.LayoutView,
    container: discord.ui.Container,
) -> None:
    for item in list(view.children):
        if isinstance(item, discord.ui.Container):
            view.remove_item(item)
    view._children.insert(0, container)


def disable_all(view: discord.ui.View | discord.ui.LayoutView) -> None:
    walker = getattr(view, "walk_children", None)
    items = walker() if callable(walker) else view.children
    for item in items:
        if hasattr(item, "disabled"):
            item.disabled = True


def simple_view(
    title: str,
    body: str | None = None,
    *,
    accent_color: discord.Colour | int | None = None,
    footer: str | None = None,
    timeout: float | None = 60,
) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=timeout)
    view.add_item(
        make_container(
            title,
            body,
            accent_color=accent_color,
            footer=footer,
        )
    )
    return view
