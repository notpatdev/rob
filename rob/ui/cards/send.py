from __future__ import annotations

import discord

from rob.models import SendRecord
from rob.ui.render import RenderedMessage, require_components_v2
from rob.ui.theme import COLOR_SEND
from rob.utils.money import format_money_from_cents, format_money_with_currency_name


def _amount_text(send: SendRecord) -> str:
    currency = (send.currency or "USD").upper()
    if currency == "USD":
        usd = format_money_from_cents(send.amount_cents, "USD")
        original_currency = (send.original_currency or "").upper()
        if send.original_amount_cents is not None and original_currency and original_currency != "USD":
            original = format_money_with_currency_name(send.original_amount_cents, original_currency)
            return f"{usd} (converted from {original})"
        return usd
    return format_money_with_currency_name(send.amount_cents, send.currency)


def send_card(
    *,
    send: SendRecord,
    domme_label: str,
    sub_display: str,
    rank: int | None = None,
    adjustment_note: str | None = None,
    throne_url: str | None = None,
):
    del rank, throne_url
    require_components_v2()
    view = discord.ui.LayoutView(timeout=1800)
    if send.source == "send_request":
        expected_fallback_note = f"Manual send via {send.method}" if send.method else "Manual send"
        lines = [
            f"**Sub:** {sub_display}",
            f"**Amount:** {_amount_text(send)}",
        ]
        if send.item_name and send.item_name != expected_fallback_note:
            lines.append(f"**Note:** {send.item_name}")
        lines.append(f"**Service:** {send.method or 'other'}")
        body = "\n\n".join(lines)
    else:
        body = (
            f"**Sub:** {sub_display}\n\n"
            f"**Amount:** {_amount_text(send)}\n\n"
            f"**Item:** {send.item_name or 'Mystery send'}"
        )
    children: list[discord.ui.Item] = [
        discord.ui.TextDisplay(f"### 💸 New Send to {domme_label}! 💸"),
    ]
    if adjustment_note:
        children.append(discord.ui.TextDisplay(adjustment_note))
    children.append(discord.ui.Separator())
    if send.item_image_url:
        children.append(
            discord.ui.Section(
                discord.ui.TextDisplay(body),
                accessory=discord.ui.Thumbnail(media=send.item_image_url),
            )
        )
    else:
        children.append(discord.ui.TextDisplay(body))
    view.add_item(discord.ui.Container(*children, accent_color=COLOR_SEND))
    return RenderedMessage(view=view)
