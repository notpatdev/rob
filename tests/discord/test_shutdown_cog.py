from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord

from rob.config.guilds import BOT_OWNER_USER_IDS, OWNER_USER_ID
from rob.discord.cogs.shutdown import ShutdownCog, _ConfirmSendView


def _forbidden() -> discord.HTTPException:
    return discord.HTTPException(
        SimpleNamespace(status=403, reason="Forbidden"), "cannot send"
    )


class _FakeUser:
    def __init__(self, user_id: int):
        self.id = user_id
        self.messages: list[dict] = []
        self.raise_on_send = False

    async def send(self, **kwargs):
        if self.raise_on_send:
            raise _forbidden()
        self.messages.append(kwargs)


class _FakeResponse:
    def __init__(self):
        self.messages: list[dict] = []
        self.deferred = False
        self.edited: dict | None = None

    async def defer(self, **kwargs):
        self.deferred = True

    async def send_message(self, *args, **kwargs):
        if args:
            kwargs = {**kwargs, "content": args[0]}
        self.messages.append(kwargs)

    async def edit_message(self, **kwargs):
        self.edited = kwargs


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
        self.edited_original: dict | None = None

    async def edit_original_response(self, **kwargs):
        self.edited_original = kwargs


def _entry(user_id: int) -> SimpleNamespace:
    return SimpleNamespace(discord_user_id=user_id)


class _FakeBot:
    def __init__(self, *, dommes=(), subs=(), blacklisted=(), users=None):
        self._dommes = list(dommes)
        self._subs = list(subs)
        self._blacklisted = set(blacklisted)
        self.users: dict[int, _FakeUser] = users or {}
        self.fetched: list[int] = []
        self.dommes_repo = SimpleNamespace(list_for_guild=self._list_dommes)
        self.subs_repo = SimpleNamespace(list_for_guild=self._list_subs)
        self.blacklist_repo = SimpleNamespace(contains=self._contains)

    async def _list_dommes(self, guild_id: int):
        return list(self._dommes)

    async def _list_subs(self, guild_id: int):
        return list(self._subs)

    async def _contains(self, user_id: int) -> bool:
        return user_id in self._blacklisted

    def get_user(self, user_id: int):
        return self.users.get(user_id)

    async def fetch_user(self, user_id: int):
        self.fetched.append(user_id)
        if user_id in self.users:
            return self.users[user_id]
        raise discord.HTTPException(SimpleNamespace(status=404, reason="Not Found"), "x")


def _confirm_button_label(view) -> str:
    def walk(items):
        for item in items:
            yield item
            children = getattr(item, "children", None)
            if children:
                yield from walk(children)

    for item in walk(view.children):
        if isinstance(item, discord.ui.Button) and (item.label or "").startswith("Send to"):
            return item.label
    raise AssertionError("no confirm button found")


def test_recipient_ids_dedup_and_exclude_blacklisted():
    bot = _FakeBot(
        dommes=[_entry(1), _entry(2)],
        subs=[_entry(2), _entry(3), _entry(4)],
        blacklisted={4},
    )
    cog = ShutdownCog(bot)
    ids = asyncio.run(cog._resolve_recipient_ids())
    # Dom/mes first, sub 2 deduped, sub 4 blacklisted-out.
    assert ids == [1, 2, 3]


def test_only_configured_owners_may_run():
    cog = ShutdownCog(_FakeBot())
    for owner_id in BOT_OWNER_USER_IDS:
        assert cog._is_owner(SimpleNamespace(id=owner_id)) is True
    assert cog._is_owner(SimpleNamespace(id=1)) is False
    assert cog._is_owner(None) is False


def test_non_owner_is_rejected_and_nothing_sent():
    user = _FakeUser(1)
    bot = _FakeBot(dommes=[_entry(1)], users={1: user})
    cog = ShutdownCog(bot)
    interaction = _FakeInteraction(user_id=42)  # not an owner

    asyncio.run(ShutdownCog.shutdown_command.callback(cog, interaction))

    assert interaction.response.messages, "non-owner should get an ephemeral error"
    assert interaction.response.messages[0]["ephemeral"] is True
    assert interaction.response.deferred is False
    assert user.messages == []  # nothing delivered


def test_owner_gets_confirmation_and_nothing_is_sent_yet():
    users = {i: _FakeUser(i) for i in (1, 2, 3)}
    bot = _FakeBot(dommes=[_entry(1), _entry(2)], subs=[_entry(3)], users=users)
    cog = ShutdownCog(bot)
    interaction = _FakeInteraction(user_id=OWNER_USER_ID)

    asyncio.run(ShutdownCog.shutdown_command.callback(cog, interaction))

    assert interaction.response.deferred is True
    # A confirmation view is shown, previewing the recipient count...
    assert len(interaction.followup.messages) == 1
    view = interaction.followup.messages[0]["view"]
    assert isinstance(view, _ConfirmSendView)
    assert _confirm_button_label(view) == "Send to 3"
    # ...but nothing has been DM'd.
    assert all(user.messages == [] for user in users.values())


def test_perform_send_delivers_to_all_and_reports():
    users = {i: _FakeUser(i) for i in (1, 2, 3)}
    bot = _FakeBot(dommes=[_entry(1), _entry(2)], subs=[_entry(3)], users=users)
    cog = ShutdownCog(bot)
    cog.send_delay_seconds = 0  # no real sleeping in tests
    interaction = _FakeInteraction(user_id=OWNER_USER_ID)

    asyncio.run(cog.perform_send(interaction))

    assert interaction.response.deferred is True
    assert all(len(user.messages) == 1 for user in users.values())
    # The ephemeral confirmation is edited into the "sent" summary.
    assert interaction.edited_original is not None
    assert "view" in interaction.edited_original


def test_deliver_counts_failures():
    good = _FakeUser(1)
    bad = _FakeUser(2)
    bad.raise_on_send = True
    cog = ShutdownCog(_FakeBot())
    cog.send_delay_seconds = 0

    sent, failed = asyncio.run(cog._deliver([good, bad]))

    assert sent == 1
    assert failed == 1
    assert len(good.messages) == 1


def test_cancel_send_delivers_nothing():
    user = _FakeUser(1)
    bot = _FakeBot(dommes=[_entry(1)], users={1: user})
    cog = ShutdownCog(bot)
    interaction = _FakeInteraction(user_id=OWNER_USER_ID)

    asyncio.run(cog.cancel_send(interaction))

    assert interaction.response.edited is not None  # confirmation replaced
    assert user.messages == []


def test_confirmation_rejects_a_different_user():
    view = _ConfirmSendView(cog=ShutdownCog(_FakeBot()), invoker_id=OWNER_USER_ID, recipient_count=5)
    other = _FakeInteraction(user_id=999)
    allowed = asyncio.run(view.interaction_check(other))
    assert allowed is False
    assert other.response.messages  # told it isn't theirs
