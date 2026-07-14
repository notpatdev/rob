from __future__ import annotations

import discord

from rob.ui.cards.shutdown import (
    FINBOT_URL,
    GRAB_DATA_URL,
    PIGEON_URL,
    shutdown_announcement_card,
    shutdown_sent_card,
)


def _iter_items(view: discord.ui.LayoutView):
    """Depth-first, document-order walk of every component in a LayoutView."""

    def _walk(items):
        for item in items:
            yield item
            children = getattr(item, "children", None)
            if children:
                yield from _walk(children)

    yield from _walk(view.children)


def _text_blocks(view: discord.ui.LayoutView) -> list[str]:
    return [
        item.content
        for item in _iter_items(view)
        if isinstance(item, discord.ui.TextDisplay)
    ]


def _link_buttons(view: discord.ui.LayoutView) -> list[discord.ui.Button]:
    return [
        item
        for item in _iter_items(view)
        if isinstance(item, discord.ui.Button)
        and item.style is discord.ButtonStyle.link
    ]


def test_announcement_has_heading_and_farewell_body():
    view = shutdown_announcement_card().view
    blocks = _text_blocks(view)
    joined = "\n".join(blocks)

    assert any(block.startswith("# Goodbye, for now...") for block in blocks)
    assert "Rob will now be shutting down" in joined
    assert "**Pigeon**" in joined
    assert "Throne username" in joined
    # The small-print timing note renders as Discord subtext.
    assert any("8am (AEST)" in block and block.startswith("-#") for block in blocks)


def test_announcement_has_two_separators_between_the_three_text_blocks():
    view = shutdown_announcement_card().view
    separators = [
        item for item in _iter_items(view) if isinstance(item, discord.ui.Separator)
    ]
    # One after the heading, one before the footer, one before the buttons.
    assert len(separators) == 3


def test_announcement_has_three_link_buttons_with_expected_urls():
    view = shutdown_announcement_card().view
    buttons = _link_buttons(view)

    labels = [button.label for button in buttons]
    urls = [button.url for button in buttons]

    assert labels == ["FinBot", "Grab your data", "Pigeon"]
    assert urls == [FINBOT_URL, GRAB_DATA_URL, PIGEON_URL]
    # Link buttons carry no custom_id, so no persistent-view registration.
    assert all(button.custom_id is None for button in buttons)


def test_announcement_view_does_not_time_out():
    # Stateless link buttons should stay clickable forever.
    assert shutdown_announcement_card().view.timeout is None


def test_sent_card_reports_counts():
    ok_view = shutdown_sent_card(sent=1, failed=0).view
    ok_text = "\n".join(_text_blocks(ok_view))
    assert "**1**" in ok_text
    assert "could not be reached" not in ok_text

    partial_view = shutdown_sent_card(sent=3, failed=2).view
    partial_text = "\n".join(_text_blocks(partial_view))
    assert "**3**" in partial_text
    assert "**2**" in partial_text
    assert "could not be reached" in partial_text
