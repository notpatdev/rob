from __future__ import annotations

from rob.throne.payloads import is_explicit_test_webhook_payload, is_known_test_sender, parse_throne_send_payload


def test_gift_purchased_price_parses_to_amount_cents():
    parsed = parse_throne_send_payload(creator_id='c', payload={'type':'gift_purchased','data':{'price':1099,'currency':'USD','item_name':'x','item_thumbnail_url':'https://x','gifter_username':'sub'}})
    assert parsed.amount_cents == 1099
    assert parsed.item_image_url == 'https://x'


def test_contribution_amount_minor_units():
    parsed = parse_throne_send_payload(creator_id='c', payload={'event_type':'contribution_purchased','data':{'amount':1500,'currency':'EUR'}})
    assert parsed.amount_cents == 1500


def test_gift_crowdfunded_allows_missing_gifter_username():
    parsed = parse_throne_send_payload(creator_id='c', payload={'type':'gift_crowdfunded','data':{'price':1099,'currency':'USD'}})
    assert parsed.gifter_username is None
    assert parsed.amount_cents == 1099


def test_explicit_test_payload_is_detected():
    payload = {'type':'webhook.test','data':{'isTest':True}}
    parsed = parse_throne_send_payload(creator_id='c', payload=payload)
    assert is_explicit_test_webhook_payload(payload, parsed) is True


def test_known_test_sender_detection():
    assert is_known_test_sender('marie_123', test_gifter_usernames={'marie_123'}) is True


def test_known_test_sender_detection_is_case_insensitive():
    assert is_known_test_sender('Marie_123', test_gifter_usernames={'marie_123'}) is True


def test_real_sender_not_known_test_sender():
    assert is_known_test_sender('real_sender', test_gifter_usernames={'marie_123'}) is False


def test_fallback_hash_is_stable_when_timestamp_missing():
    # Throne delivers at-least-once; the old now() default gave each retry a
    # different hash, so retried events without a timestamp dodged dedup.
    base = {
        "type": "gift_purchased",
        "data": {"price": 1099, "currency": "USD", "gifter_username": "sub", "orderId": "o1"},
    }
    first = parse_throne_send_payload(creator_id="c", payload=base)
    second = parse_throne_send_payload(creator_id="c", payload=base)
    assert first.fallback_event_hash == second.fallback_event_hash


def test_fallback_hash_differs_when_timestamp_present():
    with_ts = {
        "type": "gift_purchased",
        "data": {
            "price": 1099,
            "currency": "USD",
            "gifter_username": "sub",
            "orderId": "o1",
            "purchasedAt": "2026-06-01T00:00:00Z",
        },
    }
    without_ts = {
        "type": "gift_purchased",
        "data": {"price": 1099, "currency": "USD", "gifter_username": "sub", "orderId": "o1"},
    }
    a = parse_throne_send_payload(creator_id="c", payload=with_ts)
    b = parse_throne_send_payload(creator_id="c", payload=without_ts)
    assert a.fallback_event_hash != b.fallback_event_hash


def test_amount_cents_accepts_decimal_string_without_crashing():
    parsed = parse_throne_send_payload(
        creator_id="c",
        payload={"type": "gift_purchased", "data": {"amountCents": "1099.0", "currency": "USD"}},
    )
    assert parsed.amount_cents == 1099


def test_amount_cents_rounds_decimal_string():
    parsed = parse_throne_send_payload(
        creator_id="c",
        payload={"type": "gift_purchased", "data": {"amountCents": "1099.6", "currency": "USD"}},
    )
    assert parsed.amount_cents == 1100


def test_amount_cents_half_cent_tie_rounds_half_up_not_bankers():
    # ROUND_HALF_UP gives 1099; builtin round() would banker's-round to 1098.
    parsed = parse_throne_send_payload(
        creator_id="c",
        payload={"type": "gift_purchased", "data": {"amountCents": "1098.5", "currency": "USD"}},
    )
    assert parsed.amount_cents == 1099
