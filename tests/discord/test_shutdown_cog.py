from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord

from rob.config.guilds import BOT_OWNER_USER_IDS, OWNER_USER_ID
from rob.discord.cogs import shutdown as shutdown_module
from rob.discord.cogs.shutdown import ShutdownCog


class _FakeUser:
    def __init__(self, user_id: int):
        self.id = user_id
        self.messages: list[dict] = []
        self.raise_on_send = False

    async def send(self, **kwargs):
        if self.raise_on_send:
            raise discord.HTTPException(
                SimpleNamespace(status=403, reason="Forbidden"), "cannot send"
            )
        self.messages.append(kwargs)


class _FakeResponse:
    def __init__(self):
        self.messages: list[dict] = []
        self.deferred = False

    async def defer(self, **kwargs):
        self.deferred = True

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)


class _FakeFollowup:
    def __init__(self):
        self.messages: list[dict] = []

    async def send(self, **kwargs):
        self.messages.append(kwargs)


class _FakeInteraction:
    def __init__(self, user_id: int):
        self.user = SimpleNamespace(id=user_id)
        self.response = _FakeResponse()
        self.followup = _FakeFollowup()


class _FakeBot:
    def __init__(self):
        self.users: dict[int, _FakeUser] = {}
        self.fetched: list[int] = []

    def get_user(self, user_id: int):
        return self.users.get(user_id)

    async def fetch_user(self, user_id: int):
        self.fetched.append(user_id)
        if user_id in self.users:
            return self.users[user_id]
        raise discord.HTTPException(
            SimpleNamespace(status=404, reason="Not Found"), "not found"
        )


def test_recipients_are_owner_only_for_now():
    # The announcement must not reach real users while testing.
    assert shutdown_module.ANNOUNCEMENT_RECIPIENT_USER_IDS == (OWNER_USER_ID,)
    assert OWNER_USER_ID == 1299308718009356289


def test_non_owner_is_rejected_and_nothing_sent():
    bot = _FakeBot()
    owner = _FakeUser(OWNER_USER_ID)
    bot.users[OWNER_USER_ID] = owner
    cog = ShutdownCog(bot)
    interaction = _FakeInteraction(user_id=42)  # not an owner

    asyncio.run(ShutdownCog.shutdown_command.callback(cog, interaction))

    assert interaction.response.messages, "non-owner should get an ephemeral error"
    assert interaction.response.messages[0]["ephemeral"] is True
    assert interaction.response.deferred is False
    assert owner.messages == []  # nothing delivered


def test_owner_sends_announcement_to_owner_only():
    bot = _FakeBot()
    owner = _FakeUser(OWNER_USER_ID)
    bot.users[OWNER_USER_ID] = owner
    # A second, unrelated user should never receive the announcement.
    other = _FakeUser(999)
    bot.users[999] = other
    cog = ShutdownCog(bot)
    interaction = _FakeInteraction(user_id=OWNER_USER_ID)

    asyncio.run(ShutdownCog.shutdown_command.callback(cog, interaction))

    assert interaction.response.deferred is True
    assert len(owner.messages) == 1
    assert other.messages == []
    # Confirmation is ephemeral and reports one delivery.
    assert interaction.followup.messages[0]["ephemeral"] is True


def test_deliver_counts_failures():
    bot = _FakeBot()
    good = _FakeUser(1)
    bad = _FakeUser(2)
    bad.raise_on_send = True
    cog = ShutdownCog(bot)

    sent, failed = asyncio.run(cog._deliver([good, bad]))

    assert sent == 1
    assert failed == 1
    assert len(good.messages) == 1


def test_resolve_recipients_falls_back_to_fetch():
    # When the user isn't in the cache, resolution falls back to fetch_user.
    bot = _FakeBot()
    owner = _FakeUser(OWNER_USER_ID)
    bot.users[OWNER_USER_ID] = owner  # only present in the fetch store
    bot.get_user = lambda _user_id: None  # type: ignore[assignment]
    cog = ShutdownCog(bot)

    recipients = asyncio.run(cog._resolve_recipients())

    assert [r.id for r in recipients] == [OWNER_USER_ID]
    assert bot.fetched == [OWNER_USER_ID]


def test_only_configured_owners_may_run():
    bot = _FakeBot()
    cog = ShutdownCog(bot)
    for owner_id in BOT_OWNER_USER_IDS:
        assert cog._is_owner(SimpleNamespace(id=owner_id)) is True
    assert cog._is_owner(SimpleNamespace(id=1)) is False
    assert cog._is_owner(None) is False
