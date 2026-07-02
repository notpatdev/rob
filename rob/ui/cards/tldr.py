from __future__ import annotations

# Discord message content is capped at 2000 chars; the header/footer share it
# with the summary.
_CONTENT_LIMIT = 2000
_TRIM_NOTE = "… *(trimmed)*"


def tldr_message(
    *,
    channel_name: str,
    timeframe_label: str,
    summary: str,
    method: str,
    message_count: int,
    participant_count: int,
    topic: str | None = None,
    matched_count: int | None = None,
    model: str | None = None,
    ai_message_count: int | None = None,
) -> str:
    """Plain-text /tldr reply (public, no embed/card)."""

    header = f"## 🧾 TL;DR — #{channel_name} · {timeframe_label}"
    if topic:
        header += f' · "{topic}"'

    if method == "ai" and model:
        engine = f"summarised by {model} (on-server)"
    else:
        engine = "quick digest"

    people = "person" if participant_count == 1 else "people"
    msgs = "message" if message_count == 1 else "messages"
    # Be honest when a busy window didn't fully fit the model's budget: the
    # summary then covers the most recent slice, not the whole timeframe.
    if (
        method == "ai"
        and ai_message_count is not None
        and 0 < ai_message_count < message_count
    ):
        count_bit = f"latest {ai_message_count} of {message_count} {msgs}"
    else:
        count_bit = f"{message_count} {msgs}"
    footer_bits = [count_bit, f"{participant_count} {people}", engine]
    if topic and matched_count is not None:
        match_word = "match" if matched_count == 1 else "matches"
        footer_bits.insert(0, f"{matched_count} {match_word}")
    footer = "-# " + " · ".join(footer_bits)

    body = summary.strip() or "Nothing to summarise."
    budget = _CONTENT_LIMIT - len(header) - len(footer) - 2  # two newlines
    if len(body) > budget:
        body = body[: budget - len(_TRIM_NOTE)].rstrip() + _TRIM_NOTE
    return f"{header}\n{body}\n{footer}"
