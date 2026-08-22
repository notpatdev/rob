from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest

from bill.notifications import NotificationPoller, notification_nonce
from bill.worker_client import SendNotification


def notification(*, delivery_may_exist: bool = False) -> SendNotification:
    return SendNotification(
        notification_id="1",
        lease_token="lease",
        send_id="send-1",
        guild_id="100",
        channel_id="200",
        recipient_user_id="300",
        throne_handle="creator",
        amount_minor=1099,
        currency="USD",
        sender_name="sender",
        is_private=False,
        is_anonymous=False,
        item_name=None,
        item_image_url=None,
        purchased_at="2026-08-22T01:02:03Z",
        delivery_may_exist=delivery_may_exist,
    )


class FakeHistory:
    def __init__(self, messages: list[Any] | None = None) -> None:
        self.messages = messages or []

    def __aiter__(self) -> AsyncIterator[Any]:
        async def rows() -> AsyncIterator[Any]:
            for message in self.messages:
                yield message

        return rows()


class FakeChannel:
    def __init__(self, history: list[Any] | None = None) -> None:
        self.guild = SimpleNamespace(me=SimpleNamespace(id=999))
        self.sent: list[dict[str, Any]] = []
        self._history = history or []

    def history(self, *, limit: int) -> FakeHistory:
        assert limit == 500
        return FakeHistory(self._history)

    async def send(self, **kwargs: Any) -> Any:
        self.sent.append(kwargs)
        return SimpleNamespace(id=777)


class FakeBot:
    def __init__(self, channel: FakeChannel) -> None:
        self.channel = channel

    def get_channel(self, channel_id: int) -> FakeChannel:
        assert channel_id == 200
        return self.channel

    async def wait_until_ready(self) -> None:
        return None


class FakeWorker:
    def __init__(self, row: SendNotification) -> None:
        self.row = row
        self.acks: list[tuple[str, dict[str, Any]]] = []

    async def lease_notifications(self, **_kwargs: Any) -> list[SendNotification]:
        return [self.row]

    async def ack_notification(self, notification_id: str, **kwargs: Any) -> None:
        self.acks.append((notification_id, kwargs))

    async def nack_notification(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("nack should not be called")


def test_notification_nonce_is_stable_and_positive() -> None:
    assert notification_nonce("send-1") == notification_nonce("send-1")
    assert 0 <= notification_nonce("send-1") < 2**63


@pytest.mark.asyncio
async def test_poll_posts_with_stable_nonce_and_acks(monkeypatch: pytest.MonkeyPatch) -> None:
    row = notification()
    channel = FakeChannel()
    worker = FakeWorker(row)
    monkeypatch.setattr("bill.notifications.discord.TextChannel", FakeChannel)
    poller = NotificationPoller(
        bot=FakeBot(channel),  # type: ignore[arg-type]
        worker=worker,  # type: ignore[arg-type]
        owner="instance",
        interval_seconds=5,
        batch_size=10,
        lease_seconds=60,
    )

    await poller.poll_once()

    assert channel.sent[0]["nonce"] == notification_nonce(row.send_id)
    assert worker.acks == [
        ("1", {"lease_token": "lease", "discord_message_id": 777})
    ]


@pytest.mark.asyncio
async def test_poll_reconciles_existing_footer_without_reposting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = notification(delivery_may_exist=True)
    existing = SimpleNamespace(
        id=888,
        author=SimpleNamespace(id=999),
        embeds=[SimpleNamespace(footer=SimpleNamespace(text="Bill send send-1"))],
    )
    channel = FakeChannel(history=[existing])
    worker = FakeWorker(row)
    monkeypatch.setattr("bill.notifications.discord.TextChannel", FakeChannel)
    poller = NotificationPoller(
        bot=FakeBot(channel),  # type: ignore[arg-type]
        worker=worker,  # type: ignore[arg-type]
        owner="instance",
        interval_seconds=5,
        batch_size=10,
        lease_seconds=60,
    )

    await poller.poll_once()

    assert channel.sent == []
    assert worker.acks == [
        ("1", {"lease_token": "lease", "discord_message_id": 888})
    ]
