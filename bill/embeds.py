from __future__ import annotations

from datetime import datetime

import discord

from bill.worker_client import SendNotification

_ZERO_DECIMAL = {
    "BIF",
    "CLP",
    "DJF",
    "GNF",
    "JPY",
    "KRW",
    "PYG",
    "RWF",
    "UGX",
    "VND",
    "VUV",
    "XAF",
    "XOF",
    "XPF",
}
_THREE_DECIMAL = {"BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"}


def format_minor_amount(amount_minor: int, currency: str) -> str:
    code = currency.upper()
    exponent = 0 if code in _ZERO_DECIMAL else 3 if code in _THREE_DECIMAL else 2
    if exponent == 0:
        return f"{code} {amount_minor:,}"
    sign = "-" if amount_minor < 0 else ""
    absolute = abs(amount_minor)
    scale = 10**exponent
    return f"{code} {sign}{absolute // scale:,}.{absolute % scale:0{exponent}d}"


def _timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def send_footer_id(send_id: str) -> str:
    return f"Bill send {send_id}"


def build_send_embed(notification: SendNotification) -> discord.Embed:
    amount = "Private amount" if notification.is_private else format_minor_amount(
        notification.amount_minor,
        notification.currency,
    )
    embed = discord.Embed(
        title="A send landed",
        description=f"**{amount}** was sent to <@{notification.recipient_user_id}>.",
        color=discord.Color.from_rgb(99, 72, 214),
        timestamp=_timestamp(notification.purchased_at),
    )
    embed.add_field(name="Throne", value=f"@{notification.throne_handle}", inline=True)
    if notification.is_private:
        sender = "Private"
    elif notification.is_anonymous:
        sender = "Anonymous"
    else:
        sender = notification.sender_name or "Not provided"
    embed.add_field(name="From", value=sender, inline=True)
    if notification.item_name:
        embed.add_field(name="For", value=notification.item_name[:1024], inline=False)
    if notification.item_image_url:
        embed.set_thumbnail(url=notification.item_image_url)
    embed.set_footer(text=send_footer_id(notification.send_id))
    return embed
