from __future__ import annotations

from rob.ui.components import make_card, render
from rob.ui.render import RenderedMessage
from rob.ui.theme import COLOR_WARNING


def warn_dm_card(message_url: str) -> RenderedMessage:
    return render(
        make_card(
            title="⚠️ You've been warned",
            body=(
                "This is a courtesy notification that a moderation warning was recorded.\n\n"
                f"Reference: {message_url}"
            ),
            color=COLOR_WARNING,
            variant="warning",
        )
    )
