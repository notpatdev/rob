from __future__ import annotations

from rob.ui.components import make_card, render
from rob.ui.render import RenderedMessage
from rob.ui.theme import COLOR_INFO

# Components V2 text blocks cap at 4000 chars; leave headroom for chrome.
_BODY_BUDGET = 3800


def _fit(text: str, budget: int = _BODY_BUDGET) -> str:
    text = text.strip()
    if len(text) <= budget:
        return text
    note = "… (transcript trimmed)"
    return text[: budget - len(note)].rstrip() + note


def _format_duration(seconds: float | None) -> str | None:
    if not seconds or seconds <= 0:
        return None
    total = int(round(seconds))
    return f"{total // 60}:{total % 60:02d}"


def transcript_card(
    *,
    text: str,
    language: str | None = None,
    duration_seconds: float | None = None,
) -> RenderedMessage:
    body = _fit(text) if text.strip() else "_No speech detected._"

    footer_bits: list[str] = []
    duration = _format_duration(duration_seconds)
    if duration:
        footer_bits.append(duration)
    if language:
        footer_bits.append(language)
    footer_bits.append("auto-transcribed")
    footer = " · ".join(footer_bits)

    return render(
        make_card(
            title="Voice message transcript",
            body=body,
            color=COLOR_INFO,
            variant="status",
            eyebrow="🎙️ Transcript",
            footer=footer,
        )
    )
