from __future__ import annotations

from rob.ui.cards.errors import error_card
from rob.ui.components import make_card, render
from rob.ui.render import RenderedMessage
from rob.ui.theme import COLOR_SUCCESS


def admin_permission_denied_card() -> RenderedMessage:
    return error_card("You do not have permission to use this command.")


def admin_usage_card(usage: str) -> RenderedMessage:
    return error_card(f"Usage: `{usage}`")


def admin_success_card(message: str) -> RenderedMessage:
    return render(make_card(title="Admin tools", body=message, color=COLOR_SUCCESS, variant="success", eyebrow="Admin"))
