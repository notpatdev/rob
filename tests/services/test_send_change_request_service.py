from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from rob.database.repositories.models import Domme, SendChangeRequest
from rob.database.repositories.models import SendRecord
from rob.services.send_change_request_service import SendChangeRequestService


def _now() -> datetime:
    return datetime.now(timezone.utc)


class _FakeRequestsRepo:
    def __init__(self, request: SendChangeRequest):
        self.request = request
        self.delivery_calls: list[tuple[int, int, int]] = []
        self.failed_calls: list[dict] = []

    async def set_delivery(
        self,
        *,
        request_id: int,
        request_channel_id: int,
        request_message_id: int,
    ) -> SendChangeRequest:
        self.delivery_calls.append((request_id, request_channel_id, request_message_id))
        self.request = SendChangeRequest(
            **{
                **self.request.__dict__,
                "request_channel_id": request_channel_id,
                "request_message_id": request_message_id,
            }
        )
        return self.request

    async def mark_failed(self, **kwargs) -> None:
        self.failed_calls.append(kwargs)


class _FakeUser:
    def __init__(self):
        self.messages: list[dict] = []

    async def send(self, **kwargs):
        self.messages.append(kwargs)
        return SimpleNamespace(channel=SimpleNamespace(id=321), id=654)


class _FakeBot:
    def __init__(self, user):
        self._user = user
        self.bound_views: list[tuple[object, int]] = []

    def get_user(self, _discord_user_id: int):
        return self._user

    async def fetch_user(self, _discord_user_id: int):
        return self._user

    def add_view(self, view, *, message_id: int):
        self.bound_views.append((view, message_id))


def test_deliver_request_renders_send_change_card_with_action_buttons():
    now = _now()
    request = SendChangeRequest(
        id=11,
        guild_id=99,
        domme_user_id=555,
        action="send_add",
        status="pending",
        requested_by="rob@test",
        requested_sub_name="pat",
        amount_cents=2550,
        currency="USD",
        method="manual",
        note=None,
        target_send_id=None,
        decision_reason=None,
        request_channel_id=None,
        request_message_id=None,
        approved_by_user_id=None,
        approved_send_id=None,
        created_at=now,
        updated_at=now,
        decided_at=None,
    )
    domme = Domme(
        id=1,
        bot_user_id=2,
        guild_id=99,
        discord_user_id=555,
        throne_url=None,
        throne_handle="missadore",
        throne_creator_id=None,
        tracking_status="active",
        profile_status="active",
        hide_own_purchases=None,
        webhook_secret=None,
        webhook_secret_hash=None,
        webhook_connected_at=None,
        overlay_detected=False,
        last_overlay_check_at=None,
        last_successful_event_at=None,
        public_display_name="Miss Adore",
        public_display_name_updated_at=None,
        registered_at=now,
        created_at=now,
        updated_at=now,
    )
    requests_repo = _FakeRequestsRepo(request)
    user = _FakeUser()
    bot = _FakeBot(user)
    service = SendChangeRequestService(
        bot=bot,  # type: ignore[arg-type]
        requests=requests_repo,
        dommes=SimpleNamespace(),
        sends=SimpleNamespace(),
        send_service=SimpleNamespace(),
        send_queue_service=None,
        leaderboard_service=None,
    )

    delivered = asyncio.run(service._deliver_request(request, domme=domme, target_send=None))

    assert delivered.request_channel_id == 321
    assert delivered.request_message_id == 654
    assert requests_repo.delivery_calls == [(11, 321, 654)]
    assert len(user.messages) == 1
    sent_payload = user.messages[0]
    assert sent_payload["view"] is not None
    assert len(sent_payload["view"].children) >= 2
    assert bot.bound_views


def test_create_send_update_request_validates_message_id_and_stores_reason():
    now = _now()
    domme = Domme(
        id=1,
        bot_user_id=2,
        guild_id=99,
        discord_user_id=555,
        throne_url=None,
        throne_handle="missadore",
        throne_creator_id=None,
        tracking_status="active",
        profile_status="active",
        hide_own_purchases=None,
        webhook_secret=None,
        webhook_secret_hash=None,
        webhook_connected_at=None,
        overlay_detected=False,
        last_overlay_check_at=None,
        last_successful_event_at=None,
        public_display_name="Miss Adore",
        public_display_name_updated_at=None,
        registered_at=now,
        created_at=now,
        updated_at=now,
    )
    send = SendRecord(
        id=321,
        guild_id=99,
        domme_id=domme.id,
        domme_user_id=domme.discord_user_id,
        sub_id=None,
        sub_user_id=None,
        sub_name="pat",
        amount_cents=999,
        currency="USD",
        method="throne",
        source="throne_webhook",
        item_name="Gift",
        item_image_url=None,
        external_id=None,
        event_id=None,
        fallback_event_hash=None,
        is_private=False,
        seeded=False,
        sent_at=now,
        received_at=now,
        discord_post_status="posted",
        discord_posted_at=now,
        discord_message_id=654321,
        discord_post_error=None,
        created_at=now,
        is_test_send=False,
    )
    captured: dict = {}

    class _Requests:
        async def create_send_update_request(self, **kwargs):
            captured.update(kwargs)
            return SendChangeRequest(
                id=44,
                guild_id=99,
                domme_user_id=555,
                action="send_update",
                status="pending",
                requested_by=kwargs["requested_by"],
                requested_sub_name=None,
                amount_cents=kwargs["amount_cents"],
                currency=kwargs["currency"],
                method=None,
                note=kwargs["note"],
                target_send_id=kwargs["target_send_id"],
                decision_reason=None,
                request_channel_id=None,
                request_message_id=None,
                approved_by_user_id=None,
                approved_send_id=None,
                created_at=now,
                updated_at=now,
                decided_at=None,
            )

    class _Dommes:
        async def get_by_user_id(self, guild_id, user_id):
            return None

        async def get_by_handle(self, guild_id, handle):
            return domme if handle == "missadore" else None

        async def list_for_guild(self, guild_id):
            return [domme]

    class _Sends:
        async def get(self, send_id):
            return send if send_id == 321 else None

    async def _deliver_passthrough(request, *, domme, target_send):
        return request

    service = SendChangeRequestService(
        bot=SimpleNamespace(),
        requests=_Requests(),  # type: ignore[arg-type]
        dommes=_Dommes(),  # type: ignore[arg-type]
        sends=_Sends(),  # type: ignore[arg-type]
        send_service=SimpleNamespace(),
        send_queue_service=None,
        leaderboard_service=None,
    )
    service._deliver_request = _deliver_passthrough  # type: ignore[method-assign]

    created = asyncio.run(
        service.create_send_update_request(
            guild_id=99,
            domme_lookup="missadore",
            send_id=321,
            amount_cents=1875,
            message_id=654321,
            reason="Price correction",
            requested_by="Pat",
        )
    )

    assert created.action == "send_update"
    assert captured["target_send_id"] == 321
    assert captured["amount_cents"] == 1875
    assert captured["note"] == "Price correction"


def test_approve_send_update_refreshes_message_for_converted_send_and_keeps_usd_update_flow():
    now = _now()
    request = SendChangeRequest(
        id=55,
        guild_id=99,
        domme_user_id=555,
        action="send_update",
        status="pending",
        requested_by="Pat",
        requested_sub_name=None,
        amount_cents=1875,
        currency="USD",
        method=None,
        note="Price correction",
        target_send_id=321,
        decision_reason=None,
        request_channel_id=None,
        request_message_id=None,
        approved_by_user_id=None,
        approved_send_id=None,
        created_at=now,
        updated_at=now,
        decided_at=None,
    )
    target_send = SendRecord(
        id=321,
        guild_id=99,
        domme_id=1,
        domme_user_id=555,
        sub_id=None,
        sub_user_id=None,
        sub_name="pat",
        amount_cents=1198,
        currency="USD",
        method="throne",
        source="throne_webhook",
        item_name="Gift",
        item_image_url=None,
        external_id=None,
        event_id=None,
        fallback_event_hash=None,
        is_private=False,
        seeded=False,
        sent_at=now,
        received_at=now,
        discord_post_status="posted",
        discord_posted_at=now,
        discord_message_id=654321,
        discord_post_error=None,
        created_at=now,
        is_test_send=False,
        _public_send_id=None,
        original_amount_cents=1099,
        original_currency="EUR",
    )
    updated_send = SendRecord(
        **{
            **target_send.__dict__,
            "amount_cents": 1875,
            "original_amount_cents": 1099,
            "original_currency": "EUR",
        }
    )
    captured: dict[str, object] = {}

    class _Requests:
        async def get(self, request_id: int):
            return request if request_id == 55 else None

        async def mark_failed(self, **kwargs):
            captured["failed"] = kwargs

        async def mark_approved(self, **kwargs):
            captured["approved"] = kwargs
            return request

    class _Sends:
        async def get(self, send_id: int):
            return target_send if send_id == 321 else None

        async def update_amount(self, send_id: int, *, amount_cents: int, currency: str):
            captured["update_amount"] = {
                "send_id": send_id,
                "amount_cents": amount_cents,
                "currency": currency,
            }
            return updated_send

    class _Queue:
        async def refresh_send_message(self, **kwargs):
            captured["refresh_send_message"] = kwargs
            return True

    class _Leaderboard:
        async def refresh_guild(self, guild_id: int):
            captured["refresh_guild"] = guild_id

    service = SendChangeRequestService(
        bot=SimpleNamespace(),
        requests=_Requests(),  # type: ignore[arg-type]
        dommes=SimpleNamespace(),
        sends=_Sends(),  # type: ignore[arg-type]
        send_service=SimpleNamespace(),
        send_queue_service=_Queue(),  # type: ignore[arg-type]
        leaderboard_service=_Leaderboard(),  # type: ignore[arg-type]
    )

    rendered = asyncio.run(service.approve_request(request_id=55, approved_by_user_id=555))

    assert rendered is not None
    assert captured["update_amount"] == {
        "send_id": 321,
        "amount_cents": 1875,
        "currency": "USD",
    }
    refresh_call = captured["refresh_send_message"]
    assert refresh_call["send_id"] == 321
    assert refresh_call["message_id"] == 654321
    assert "adjusted by Pat" in refresh_call["adjustment_note"]
    assert captured["refresh_guild"] == 99
