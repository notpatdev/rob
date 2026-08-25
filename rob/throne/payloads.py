from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Any


ACCEPTED_EVENT_TYPES = {
    "gift_purchased",
    "contribution_purchased",
    "gift_crowdfunded",
    "item_purchased",
}


@dataclass(frozen=True)
class ThroneSendPayload:
    event_id: str | None
    event_type: str | None
    order_id: str | None
    gifter_username: str | None
    item_name: str | None
    item_image_url: str | None
    amount_cents: int
    currency: str
    is_private: bool
    purchased_at: datetime
    fallback_event_hash: str


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _first_str(*values: Any) -> str | None:
    value = _first(*values)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _truthy(*values: Any) -> bool:
    for value in values:
        if bool(value):
            return True
    return False


def is_supported_event_type(event_type: str | None) -> bool:
    if not event_type:
        return True
    return event_type in ACCEPTED_EVENT_TYPES


def parse_timestamp_opt(value: Any) -> datetime | None:
    """Return None for a missing or unparseable timestamp.

    The fallback dedup hash must stay stable across Throne's at-least-once
    retries, so this must not default to now().
    """
    if value is None:
        return None

    text = str(value).strip()
    if not text:
        return None

    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_timestamp(value: Any) -> datetime:
    return parse_timestamp_opt(value) or datetime.now(timezone.utc)


def _to_int_cents(value: Any) -> int:
    """Round minor-unit money to int cents with Decimal ROUND_HALF_UP, matching
    the FX path; float round() would give round(1098.5) -> 1098."""
    if value is None:
        return 0
    text = str(value).strip().replace("$", "").replace(",", "")
    if not text:
        return 0
    try:
        return int(Decimal(text).to_integral_value(rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        return 0


def _money_to_cents(value: Any, *, event_type: str | None) -> int:
    return _to_int_cents(value)


def build_fallback_hash(
    *,
    creator_id: str,
    order_id: str | None,
    purchased_at: datetime | None,
    gifter_username: str | None,
    item_name: str | None,
    amount_cents: int,
    currency: str,
) -> str:
    # purchased_at is None when Throne sent no parseable timestamp; omit it
    # rather than substitute now() so retries of the same event hash the same.
    raw = "|".join(
        [
            creator_id,
            order_id or "",
            purchased_at.isoformat() if purchased_at is not None else "",
            gifter_username or "",
            item_name or "",
            str(amount_cents),
            currency,
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return None


def is_explicit_test_webhook_payload(payload: dict[str, Any], parsed: ThroneSendPayload | None = None) -> bool:
    data = _as_dict(payload.get("data"))
    event_type = _first_str(payload.get("type"), payload.get("eventType"), payload.get("event_type"), parsed.event_type if parsed else None)
    event_name = _first_str(payload.get("event"), data.get("event"))
    payload_kind = _first_str(payload.get("kind"), payload.get("payloadType"), payload.get("payload_type"))

    for text in (event_type, event_name, payload_kind):
        if text and "test" in text.lower():
            return True

    boolean_flags = [
        payload.get("test"),
        payload.get("isTest"),
        payload.get("is_test"),
        data.get("test"),
        data.get("isTest"),
        data.get("is_test"),
    ]
    if any(_as_bool(v) is True for v in boolean_flags):
        return True

    item_name = (parsed.item_name if parsed else None) or _first_str(data.get("itemName"), payload.get("itemName"))
    order_id = (parsed.order_id if parsed else None) or _first_str(data.get("orderId"), payload.get("orderId"))
    joined = f"{item_name or ''} {order_id or ''}".lower()
    return any(token in joined for token in ("test", "webhook", "setup"))


def is_known_test_sender(gifter_username: str | None, *, test_gifter_usernames: set[str] | None = None) -> bool:
    usernames = {u.strip().lower() for u in (test_gifter_usernames or {"marie_123"}) if u.strip()}
    if not usernames or not gifter_username:
        return False
    return gifter_username.strip().lower() in usernames


def is_test_webhook_payload(
    payload: dict[str, Any],
    parsed: ThroneSendPayload | None = None,
    *,
    test_gifter_usernames: set[str] | None = None,
) -> bool:
    return is_explicit_test_webhook_payload(payload, parsed) or is_known_test_sender((parsed.gifter_username if parsed else None), test_gifter_usernames=test_gifter_usernames)


def parse_throne_send_payload(
    *,
    creator_id: str,
    payload: dict[str, Any],
) -> ThroneSendPayload:
    data = _as_dict(payload.get("data"))

    event_id = _first_str(
        payload.get("id"),
        payload.get("eventId"),
        payload.get("event_id"),
    )
    event_type = _first_str(
        payload.get("type"),
        payload.get("eventType"),
        payload.get("event_type"),
    )
    order_id = _first_str(
        data.get("orderId"),
        data.get("order_id"),
        payload.get("orderId"),
        payload.get("order_id"),
    )

    parsed_purchased_at = parse_timestamp_opt(
        _first(
            data.get("purchasedAt"),
            data.get("purchased_at"),
            data.get("createdAt"),
            data.get("created_at"),
            data.get("timestamp"),
            payload.get("purchasedAt"),
            payload.get("purchased_at"),
            payload.get("createdAt"),
            payload.get("created_at"),
            payload.get("timestamp"),
        )
    )
    purchased_at = parsed_purchased_at or datetime.now(timezone.utc)

    gifter = {}
    for container in (data, payload):
        for key in ("gifter", "sender", "user"):
            candidate = container.get(key)
            if isinstance(candidate, dict):
                gifter = candidate
                break
        if gifter:
            break

    gifter_username = _first_str(
        gifter.get("username"),
        gifter.get("name"),
        data.get("gifterUsername"),
        data.get("gifter_username"),
        data.get("senderUsername"),
        data.get("sender_username"),
        data.get("senderName"),
        data.get("sender_name"),
        payload.get("gifterUsername"),
        payload.get("gifter_username"),
        payload.get("senderUsername"),
        payload.get("sender_username"),
        payload.get("senderName"),
        payload.get("sender_name"),
    )

    is_anonymous = _truthy(
        gifter.get("isAnonymous"),
        data.get("isAnonymous"),
        data.get("is_anonymous"),
        data.get("anonymous"),
        payload.get("isAnonymous"),
        payload.get("is_anonymous"),
        payload.get("anonymous"),
    )
    if is_anonymous:
        gifter_username = "Anonymous"

    if event_type == "gift_crowdfunded" and not gifter_username:
        gifter_username = None

    item = {}
    for container in (data, payload):
        for key in ("gift", "item", "product", "wishlistItem"):
            candidate = container.get(key)
            if isinstance(candidate, dict):
                item = candidate
                break
        if item:
            break

    item_name = _first_str(
        item.get("name"),
        item.get("title"),
        data.get("itemName"),
        data.get("item_name"),
        payload.get("itemName"),
        payload.get("item_name"),
        payload.get("productName"),
        payload.get("product_name"),
        payload.get("giftName"),
        payload.get("gift_name"),
    )
    item_image_url = _first_str(
        item.get("imageUrl"),
        item.get("image_url"),
        item.get("image"),
        data.get("itemImageUrl"),
        data.get("item_image_url"),
        data.get("itemThumbnailUrl"),
        data.get("item_thumbnail_url"),
        payload.get("itemImageUrl"),
        payload.get("item_image_url"),
        payload.get("itemThumbnailUrl"),
        payload.get("item_thumbnail_url"),
    )
    if item_image_url and not item_image_url.startswith(("http://", "https://")):
        item_image_url = None

    currency = (
        _first_str(data.get("currency"), payload.get("currency"), item.get("currency"))
        or "USD"
    )

    amount_source = _first(
        data.get("amountCents"),
        data.get("amount_cents"),
        payload.get("amountCents"),
        payload.get("amount_cents"),
        payload.get("priceCents"),
        payload.get("price_cents"),
        item.get("amountCents"),
        item.get("amount_cents"),
    )
    if amount_source is not None:
        # Throne usually sends integer cents, but decimal/float-like values
        # ("1099.5") show up too; must not raise out of the webhook handler.
        amount_cents = _to_int_cents(amount_source)
    else:
        amount_cents = _money_to_cents(
            _first(
                data.get("price"),
                payload.get("price"),
                data.get("amount"),
                payload.get("amount"),
                item.get("amount"),
                data.get("amountUsd"),
                data.get("amount_usd"),
                data.get("priceUsd"),
                data.get("price_usd"),
                payload.get("amountUsd"),
                payload.get("amount_usd"),
                payload.get("priceUsd"),
                payload.get("price_usd"),
            ),
            event_type=event_type,
        )

    is_private = _truthy(
        data.get("isPrivate"),
        data.get("is_private"),
        data.get("amountHidden"),
        data.get("hideAmount"),
        payload.get("isPrivate"),
        payload.get("is_private"),
        payload.get("amountHidden"),
        payload.get("hideAmount"),
    )
    if is_private:
        amount_cents = 0

    fallback_event_hash = build_fallback_hash(
        creator_id=creator_id,
        order_id=order_id,
        purchased_at=parsed_purchased_at,
        gifter_username=gifter_username,
        item_name=item_name,
        amount_cents=amount_cents,
        currency=currency,
    )

    return ThroneSendPayload(
        event_id=event_id,
        event_type=event_type,
        order_id=order_id,
        gifter_username=gifter_username,
        item_name=item_name,
        item_image_url=item_image_url,
        amount_cents=amount_cents,
        currency=currency,
        is_private=is_private,
        purchased_at=purchased_at,
        fallback_event_hash=fallback_event_hash,
    )
