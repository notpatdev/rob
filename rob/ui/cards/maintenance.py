from __future__ import annotations

from rob.ui.components import make_card, render
from rob.ui.copy import MAINTENANCE_FOOTER
from rob.ui.render import CardSection, RenderedMessage
from rob.ui.theme import COLOR_WARNING


def maintenance_embed(reason: str | None) -> RenderedMessage:
    return render(
        make_card(
            title="Rob | Maintenance Mode",
            body="New sends are being saved, but Discord posting is paused.",
            color=COLOR_WARNING,
            footer=MAINTENANCE_FOOTER,
            sections=[CardSection(title="Reason", text=reason or "No reason provided.")],
            variant="warning",
            eyebrow="Maintenance",
        )
    )


def rob_offline_embed() -> RenderedMessage:
    return render(
        make_card(
            title="Send Tracking Offline",
            body=(
                "-# Rob's future is being decided. He may be back soon"
            ),
            color=COLOR_WARNING,
            variant="warning",
            eyebrow="...",
        )
    )
