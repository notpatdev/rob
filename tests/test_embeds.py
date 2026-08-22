from __future__ import annotations

from bill.embeds import build_send_embed, format_minor_amount, send_footer_id
from bill.worker_client import SendNotification


def notification(**overrides: object) -> SendNotification:
    values: dict[str, object] = {
        "notification_id": "1",
        "lease_token": "lease",
        "send_id": "send-1",
        "guild_id": "100",
        "channel_id": "200",
        "recipient_user_id": "300",
        "throne_handle": "creator",
        "amount_minor": 1099,
        "currency": "USD",
        "sender_name": "sender",
        "is_private": False,
        "is_anonymous": False,
        "item_name": "A gift",
        "item_image_url": "https://example.com/gift.png",
        "purchased_at": "2026-08-22T01:02:03Z",
        "delivery_may_exist": False,
    }
    values.update(overrides)
    return SendNotification(**values)  # type: ignore[arg-type]


def test_formats_currency_minor_units() -> None:
    assert format_minor_amount(1099, "usd") == "USD 10.99"
    assert format_minor_amount(1099, "JPY") == "JPY 1,099"
    assert format_minor_amount(1099, "KWD") == "KWD 1.099"


def test_normal_send_embed_contains_identity_and_marker() -> None:
    embed = build_send_embed(notification())

    assert "USD 10.99" in (embed.description or "")
    assert "<@300>" in (embed.description or "")
    assert embed.fields[1].value == "sender"
    assert embed.footer.text == send_footer_id("send-1")


def test_private_send_embed_hides_amount_and_sender() -> None:
    embed = build_send_embed(
        notification(is_private=True, sender_name="must-not-appear", amount_minor=999999)
    )

    rendered = embed.to_dict()
    assert "Private amount" in rendered["description"]
    assert "must-not-appear" not in str(rendered)
    assert embed.fields[1].value == "Private"


def test_anonymous_send_embed_hides_sender_name() -> None:
    embed = build_send_embed(notification(is_anonymous=True, sender_name=None))

    assert embed.fields[1].value == "Anonymous"
