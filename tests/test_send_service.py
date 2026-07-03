from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from types import SimpleNamespace

from rob.database.repositories.models import NewSend, SendRecord, ThroneCreator
from rob.services.send_service import SendService
from rob.throne.payloads import ThroneSendPayload
from rob.utils.fx import convert_cents_to_usd


@dataclass
class _FakeMaintenance:
    enabled: bool = False

    async def is_enabled(self) -> bool:
        return self.enabled


class _FakeSendsRepo:
    def __init__(self) -> None:
        self.inserted: NewSend | None = None

    async def insert(self, send: NewSend) -> SendRecord:
        self.inserted = send
        now = datetime.now(timezone.utc)
        return SendRecord(
            1,
            send.guild_id,
            send.domme_id,
            send.domme_user_id,
            send.sub_id,
            send.sub_user_id,
            send.sub_name,
            send.amount_cents,
            send.currency,
            send.method,
            send.source,
            send.item_name,
            send.item_image_url,
            send.external_id,
            send.event_id,
            send.fallback_event_hash,
            send.is_private,
            send.seeded,
            send.sent_at,
            now,
            send.discord_post_status,
            None,
            None,
            None,
            now,
            send.is_test_send,
        )


class _FakeSubsRepo:
    def __init__(self, *, returned_sub=None, sub_by_user_id=None):
        self.returned_sub = returned_sub
        self.sub_by_user_id = sub_by_user_id
        self.lookup_calls: list[tuple[int, str]] = []
        self.user_id_calls: list[tuple[int, int]] = []

    async def get_by_send_name(self, guild_id: int, send_name: str):
        self.lookup_calls.append((guild_id, send_name))
        return self.returned_sub

    async def get_by_name(self, guild_id: int, send_name: str):
        return await self.get_by_send_name(guild_id, send_name)

    async def get_by_user_id(self, guild_id: int, discord_user_id: int):
        self.user_id_calls.append((guild_id, discord_user_id))
        return self.sub_by_user_id


class _FakeThroneService:
    def __init__(self, match=None) -> None:
        self.match = match
        self.calls: list[tuple[str, str | None, str | None]] = []

    async def match_item(self, *, creator_id: str, item_name: str | None, item_image_url: str | None):
        self.calls.append((creator_id, item_name, item_image_url))
        return self.match


def _creator(guild_id: int = 1) -> ThroneCreator:
    now = datetime.now(timezone.utc)
    return ThroneCreator(
        1,
        guild_id,
        1,
        10,
        "pat",
        "creator-id",
        False,
        "webhook",
        None,
        None,
        None,
        False,
        None,
        None,
        None,
        None,
        now,
        now,
    )


def _payload(gifter_username: str | None) -> ThroneSendPayload:
    now = datetime.now(timezone.utc)
    return ThroneSendPayload(
        event_id="evt_1",
        event_type="gift_purchased",
        order_id="order_1",
        gifter_username=gifter_username,
        item_name="Flowers",
        item_image_url="https://example.com/item.png",
        amount_cents=1099,
        currency="USD",
        is_private=False,
        purchased_at=now,
        fallback_event_hash="hash_1",
    )


def _payload_non_usd(gifter_username: str | None) -> ThroneSendPayload:
    now = datetime.now(timezone.utc)
    return ThroneSendPayload(
        event_id="evt_2",
        event_type="gift_purchased",
        order_id="order_2",
        gifter_username=gifter_username,
        item_name="Flowers",
        item_image_url="https://example.com/item.png",
        amount_cents=1099,
        currency="EUR",
        is_private=False,
        purchased_at=now,
        fallback_event_hash="hash_2",
    )


def test_known_test_sender_is_stored_as_test_send():
    sends = _FakeSendsRepo()
    subs = _FakeSubsRepo()
    service = SendService(
        sends=sends,
        subs=subs,
        maintenance=_FakeMaintenance(),
        throne_test_gifter_usernames=("marie_123",),
    )

    asyncio.run(service.record_throne_send(creator=_creator(), payload=_payload("marie_123")))

    assert sends.inserted is not None
    assert sends.inserted.is_test_send is True
    assert sends.inserted.item_image_url == "https://example.com/item.png"


def test_real_sender_is_not_stored_as_test_send():
    sends = _FakeSendsRepo()
    subs = _FakeSubsRepo()
    service = SendService(
        sends=sends,
        subs=subs,
        maintenance=_FakeMaintenance(),
        throne_test_gifter_usernames=("marie_123",),
    )

    asyncio.run(service.record_throne_send(creator=_creator(), payload=_payload("real_sender")))

    assert sends.inserted is not None
    assert sends.inserted.is_test_send is False


def test_sub_alias_lookup_sets_sub_user_id():
    sends = _FakeSendsRepo()
    sub = type("Sub", (), {"id": 7, "discord_user_id": 99, "send_name": "alias"})
    subs = _FakeSubsRepo(returned_sub=sub)
    service = SendService(
        sends=sends,
        subs=subs,
        maintenance=_FakeMaintenance(),
        throne_test_gifter_usernames=("marie_123",),
    )

    asyncio.run(service.record_throne_send(creator=_creator(), payload=_payload("alias")))

    assert sends.inserted is not None
    assert sends.inserted.sub_id == 7
    assert sends.inserted.sub_user_id == 99
    assert subs.lookup_calls == [(1, "alias")]


def test_anonymous_sender_does_not_attempt_sub_alias_lookup():
    sends = _FakeSendsRepo()
    subs = _FakeSubsRepo()
    service = SendService(
        sends=sends,
        subs=subs,
        maintenance=_FakeMaintenance(),
        throne_test_gifter_usernames=("marie_123",),
    )

    asyncio.run(service.record_throne_send(creator=_creator(), payload=_payload("anonymous")))

    assert sends.inserted is not None
    assert sends.inserted.sub_id is None
    assert sends.inserted.sub_user_id is None
    assert subs.lookup_calls == []


def test_non_usd_throne_send_is_converted_to_usd_with_original_metadata():
    sends = _FakeSendsRepo()
    subs = _FakeSubsRepo()
    service = SendService(
        sends=sends,
        subs=subs,
        maintenance=_FakeMaintenance(),
        throne_test_gifter_usernames=("marie_123",),
    )

    asyncio.run(service.record_throne_send(creator=_creator(), payload=_payload_non_usd("euro_sender")))

    assert sends.inserted is not None
    assert sends.inserted.currency == "USD"
    assert sends.inserted.amount_cents == convert_cents_to_usd(1099, "EUR")
    assert sends.inserted.amount_cents != 1099
    assert sends.inserted.original_amount_cents == 1099
    assert sends.inserted.original_currency == "EUR"


def test_unsupported_currency_throne_send_is_recorded_as_ignored_not_dropped():
    # An unknown currency must not 500 the webhook handler (Throne would retry
    # forever) or silently drop the send; it is recorded as ignored.
    sends = _FakeSendsRepo()
    subs = _FakeSubsRepo()
    service = SendService(
        sends=sends,
        subs=subs,
        maintenance=_FakeMaintenance(),
        throne_test_gifter_usernames=("marie_123",),
    )
    payload = ThroneSendPayload(
        event_id="evt_x",
        event_type="gift_purchased",
        order_id="order_x",
        gifter_username="real_sender",
        item_name="Flowers",
        item_image_url=None,
        amount_cents=5000,
        currency="ZZZ",
        is_private=False,
        purchased_at=datetime.now(timezone.utc),
        fallback_event_hash="hash_x",
    )

    record = asyncio.run(service.record_throne_send(creator=_creator(), payload=payload))

    assert record is not None  # not dropped, no exception
    assert sends.inserted is not None
    assert sends.inserted.discord_post_status == "ignored"
    assert sends.inserted.amount_cents == 0
    assert sends.inserted.currency == "USD"
    assert sends.inserted.original_amount_cents == 5000
    assert sends.inserted.original_currency == "ZZZ"


def test_main_guild_offline_throne_send_is_saved_without_discord_queue():
    from rob.config.guilds import MAIN_GUILD_ID

    sends = _FakeSendsRepo()
    subs = _FakeSubsRepo()

    class OfflineMaintenance(_FakeMaintenance):
        async def send_tracking_disabled_for_guild(self, guild_id: int | None) -> bool:
            return guild_id == MAIN_GUILD_ID

    creator = _creator(MAIN_GUILD_ID)
    service = SendService(
        sends=sends,
        subs=subs,
        maintenance=OfflineMaintenance(),
    )

    asyncio.run(service.record_throne_send(creator=creator, payload=_payload("real_sender")))

    assert sends.inserted is not None
    assert sends.inserted.discord_post_status == "posted"


def test_dev_guild_offline_mode_does_not_change_throne_queue_status():
    from rob.config.guilds import TEST_GUILD_ID

    sends = _FakeSendsRepo()
    subs = _FakeSubsRepo()

    class OfflineMaintenance(_FakeMaintenance):
        async def send_tracking_disabled_for_guild(self, guild_id: int | None) -> bool:
            return False

    creator = _creator(TEST_GUILD_ID)
    service = SendService(
        sends=sends,
        subs=subs,
        maintenance=OfflineMaintenance(),
    )

    asyncio.run(service.record_throne_send(creator=creator, payload=_payload("real_sender")))

    assert sends.inserted is not None
    assert sends.inserted.discord_post_status == "pending"


def _record_manual_send(service: SendService, **overrides):
    kwargs = dict(
        guild_id=1,
        domme_id=2,
        domme_user_id=10,
        amount_cents=1000,
        currency="USD",
        method="cashapp",
        note=None,
    )
    kwargs.update(overrides)
    return asyncio.run(service.record_manual_send(**kwargs))


def test_manual_send_with_user_mention_links_to_user():
    # A sub picked from the @-autocomplete arrives as free text "<@555>"; it
    # must attribute to that user, not render as "@User with no nickname claimed".
    sends = _FakeSendsRepo()
    subs = _FakeSubsRepo(sub_by_user_id=None)
    service = SendService(sends=sends, subs=subs, maintenance=_FakeMaintenance())

    _record_manual_send(service, sub_name="<@555>")

    assert sends.inserted is not None
    assert sends.inserted.sub_user_id == 555
    assert sends.inserted.sub_name is None
    assert subs.user_id_calls == [(1, 555)]
    # The raw mention must never reach the send-name lookup.
    assert subs.lookup_calls == []


def test_manual_send_with_legacy_nickname_mention_links_to_user():
    sends = _FakeSendsRepo()
    subs = _FakeSubsRepo(sub_by_user_id=None)
    service = SendService(sends=sends, subs=subs, maintenance=_FakeMaintenance())

    _record_manual_send(service, sub_name="<@!777>")

    assert sends.inserted is not None
    assert sends.inserted.sub_user_id == 777


def test_manual_send_mention_to_registered_sub_uses_registered_send_name():
    sends = _FakeSendsRepo()
    sub = type("Sub", (), {"id": 7, "discord_user_id": 555, "send_name": "kitten"})
    subs = _FakeSubsRepo(sub_by_user_id=sub)
    service = SendService(sends=sends, subs=subs, maintenance=_FakeMaintenance())

    _record_manual_send(service, sub_name="<@555>")

    assert sends.inserted is not None
    assert sends.inserted.sub_id == 7
    assert sends.inserted.sub_user_id == 555
    assert sends.inserted.sub_name == "kitten"


def test_manual_send_explicit_sub_user_id_keeps_provided_display_name():
    # The slash command passes the resolved display name alongside an explicit
    # sub_user_id.
    sends = _FakeSendsRepo()
    sub = type("Sub", (), {"id": 9, "discord_user_id": 555, "send_name": "kitten"})
    subs = _FakeSubsRepo(sub_by_user_id=sub)
    service = SendService(sends=sends, subs=subs, maintenance=_FakeMaintenance())

    _record_manual_send(service, sub_name="DisplayName", sub_user_id=555)

    assert sends.inserted is not None
    assert sends.inserted.sub_id == 9
    assert sends.inserted.sub_user_id == 555
    assert sends.inserted.sub_name == "DisplayName"
    assert subs.lookup_calls == []


def test_manual_send_plain_name_still_matches_by_send_name():
    sends = _FakeSendsRepo()
    sub = type("Sub", (), {"id": 3, "discord_user_id": 42, "send_name": "kitten"})
    subs = _FakeSubsRepo(returned_sub=sub)
    service = SendService(sends=sends, subs=subs, maintenance=_FakeMaintenance())

    _record_manual_send(service, sub_name="kitten")

    assert sends.inserted is not None
    assert sends.inserted.sub_id == 3
    assert sends.inserted.sub_user_id == 42
    assert sends.inserted.sub_name == "kitten"
    assert subs.lookup_calls == [(1, "kitten")]
    assert subs.user_id_calls == []


def test_manual_send_plain_unmatched_name_is_left_unclaimed():
    sends = _FakeSendsRepo()
    subs = _FakeSubsRepo(returned_sub=None)
    service = SendService(sends=sends, subs=subs, maintenance=_FakeMaintenance())

    _record_manual_send(service, sub_name="stranger")

    assert sends.inserted is not None
    assert sends.inserted.sub_user_id is None
    assert sends.inserted.sub_name == "stranger"


def test_gift_purchased_without_visible_amount_uses_wishlist_match_and_converts_to_usd():
    sends = _FakeSendsRepo()
    subs = _FakeSubsRepo()
    throne = _FakeThroneService(
        match=SimpleNamespace(
            amount_cents=1099,
            currency="EUR",
        )
    )
    service = SendService(
        sends=sends,
        subs=subs,
        maintenance=_FakeMaintenance(),
        throne=throne,
    )
    hidden_amount_payload = ThroneSendPayload(
        event_id="evt_hidden",
        event_type="gift_purchased",
        order_id="order_hidden",
        gifter_username="anonymous",
        item_name="Flowers",
        item_image_url="https://example.com/item.png",
        amount_cents=0,
        currency="USD",
        is_private=True,
        purchased_at=datetime.now(timezone.utc),
        fallback_event_hash="hash_hidden",
    )

    asyncio.run(service.record_throne_send(creator=_creator(), payload=hidden_amount_payload))

    assert sends.inserted is not None
    assert sends.inserted.amount_cents == convert_cents_to_usd(1099, "EUR")
    assert sends.inserted.currency == "USD"
    assert sends.inserted.original_amount_cents == 1099
    assert sends.inserted.original_currency == "EUR"
    assert sends.inserted.is_private is False
    assert throne.calls == [("creator-id", "Flowers", "https://example.com/item.png")]
