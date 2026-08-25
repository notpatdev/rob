from __future__ import annotations

import discord

from rob.database.repositories.public_summary import (
    CurrencyTotal,
    GuildSummary,
    TopReceiver,
)
from rob.ui.cards.final_sequence import (
    final_stats_card,
    final_thank_you_card,
)
from rob.ui.cards.shutdown import FINBOT_URL, PIGEON_URL, ROB_WEBSITE_URL

# Like the shutdown cards, the wording here is owner-editable prose. These tests
# assert card *structure* (buttons, the presence of the real numbers) rather
# than exact copy, so Pat can reword the farewell / stats freely.


def _iter_items(view: discord.ui.LayoutView):
    def _walk(items):
        for item in items:
            yield item
            children = getattr(item, "children", None)
            if children:
                yield from _walk(children)

    yield from _walk(view.children)


def _text(view: discord.ui.LayoutView) -> str:
    return "\n".join(
        item.content
        for item in _iter_items(view)
        if isinstance(item, discord.ui.TextDisplay)
    )


def _link_buttons(view: discord.ui.LayoutView) -> list[discord.ui.Button]:
    return [
        item
        for item in _iter_items(view)
        if isinstance(item, discord.ui.Button)
        and item.style is discord.ButtonStyle.link
    ]


def _summary(**overrides) -> GuildSummary:
    base = dict(
        last_updated=None,
        total_count=3,
        domme_count=4,
        sub_count=7,
        totals=[
            CurrencyTotal("USD", 150000, 2),
            CurrencyTotal("EUR", 5000, 1),
        ],
        top_receivers=[
            TopReceiver("Miss X", 100000, "USD", 1),
            TopReceiver("Miss Y", 50000, "USD", 1),
        ],
    )
    base.update(overrides)
    return GuildSummary(**base)


# --- Thank-you DM ----------------------------------------------------------


def test_thank_you_card_has_the_three_link_buttons():
    view = final_thank_you_card().view
    urls = [button.url for button in _link_buttons(view)]
    assert urls == [FINBOT_URL, ROB_WEBSITE_URL, PIGEON_URL]


def test_thank_you_card_has_no_accent_and_never_times_out():
    view = final_thank_you_card().view
    containers = [
        item for item in _iter_items(view) if isinstance(item, discord.ui.Container)
    ]
    assert containers
    assert all(container.accent_color is None for container in containers)
    assert view.timeout is None


def test_thank_you_card_has_a_heading_and_body():
    view = final_thank_you_card().view
    text = _text(view)
    assert text.lstrip().startswith("#")
    assert text.strip()


# --- Final stats -----------------------------------------------------------


def test_stats_card_surfaces_the_real_numbers():
    view = final_stats_card(_summary()).view
    text = _text(view)
    # Totals, formatted with symbol + thousands separator.
    assert "$1,500.00" in text
    assert "€50.00" in text
    # People counts.
    assert "**4**" in text  # dommes
    assert "**7**" in text  # subs
    # Top receiver appears.
    assert "Miss X" in text
    # Total-count "for a laugh" line.
    assert "**3**" in text


def test_stats_card_handles_an_empty_server():
    view = final_stats_card(
        _summary(total_count=0, totals=[], top_receivers=[], domme_count=0, sub_count=0)
    ).view
    text = _text(view)
    assert "No sends were counted." in text
    assert "No receivers to list." in text


def test_stats_card_limits_top_receivers_to_five():
    many = [TopReceiver(f"Miss {i}", 1000 * (10 - i), "USD", 1) for i in range(8)]
    view = final_stats_card(_summary(top_receivers=many)).view
    text = _text(view)
    assert "Miss 0" in text
    assert "Miss 4" in text
    # Sixth-ranked receiver onward is not listed.
    assert "Miss 5" not in text
