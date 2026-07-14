from __future__ import annotations

import discord

from rob.ui.cards.shutdown import (
    FINBOT_URL,
    PIGEON_URL,
    ROB_WEBSITE_URL,
    shutdown_announcement_card,
    shutdown_sent_card,
)

# NOTE: The announcement wording is owner-editable prose that changes often.
# These tests deliberately assert card *structure* (sections, styling, buttons)
# rather than exact copy, so editing the message text never breaks the build /
# deploy. The URLs are compared against the module constants (imported below),
# so changing a link constant doesn't break these tests either.


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


def test_announcement_is_heading_plus_three_sections():
    # Structure only (no exact wording): a heading followed by intro, timeline,
    # and closing — four non-empty text blocks, the first a markdown heading.
    view = shutdown_announcement_card().view
    blocks = _text_blocks(view)

    assert len(blocks) == 4
    assert blocks[0].lstrip().startswith("#")
    assert all(block.strip() for block in blocks)


def test_announcement_has_no_accent_colour():
    # "No side colour" — the announcement container must not set an accent.
    view = shutdown_announcement_card().view
    containers = [
        item for item in _iter_items(view) if isinstance(item, discord.ui.Container)
    ]
    assert containers, "expected a Container in the announcement"
    assert all(container.accent_color is None for container in containers)


def test_announcement_uses_large_section_separators():
    view = shutdown_announcement_card().view
    separators = [
        item for item in _iter_items(view) if isinstance(item, discord.ui.Separator)
    ]
    # After heading, intro, timeline, and closing (before the buttons).
    assert len(separators) == 4
    assert all(
        separator.spacing is discord.SeparatorSpacing.large for separator in separators
    )


def test_announcement_has_three_link_buttons_to_configured_urls():
    view = shutdown_announcement_card().view
    buttons = _link_buttons(view)

    urls = [button.url for button in buttons]
    # URLs come from module constants (imported here), so editing a link
    # constant keeps this test passing.
    assert urls == [FINBOT_URL, ROB_WEBSITE_URL, PIGEON_URL]
    # Every button has a visible label and no custom_id (stateless link button).
    assert all(button.label and button.label.strip() for button in buttons)
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
