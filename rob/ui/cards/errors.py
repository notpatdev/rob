from __future__ import annotations

from rob.ui.components import make_card, render
from rob.ui.copy import PERMISSION_ROLE_MISSING
from rob.ui.theme import COLOR_DANGER


def error_card(message: str, detail: str | None = None):
    return render(
        make_card(
            title=message,
            body=detail or "If this keeps happening, let a staff member know.",
            color=COLOR_DANGER,
            variant="error",
            eyebrow="⚠️ Heads up",
        )
    )


def error_permission(detail: str = PERMISSION_ROLE_MISSING):
    return render(
        make_card(
            title="That's not available on this account",
            body=detail,
            color=COLOR_DANGER,
            variant="error",
            eyebrow="⚠️ Permission needed",
        )
    )
