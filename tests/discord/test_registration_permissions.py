from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import discord

from rob.discord.cogs.registration import RegistrationCog, YesButton


class _FakeResponse:
    def __init__(self):
        self.messages: list[dict] = []
        self.deferred = False
        self.modal = None
        self.edits: list[dict] = []

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)

    async def defer(self, *, ephemeral: bool = False):
        self.deferred = ephemeral

    async def send_modal(self, modal):
        self.modal = modal

    async def edit_message(self, **kwargs):
        self.edits.append(kwargs)


class _FakeFollowup:
    def __init__(self):
        self.messages: list[dict] = []

    async def send(self, **kwargs):
        self.messages.append(kwargs)


class _FakePartialMessage:
    def __init__(self, message_id: int):
        self.id = message_id
        self.edits: list[dict] = []

    async def edit(self, **kwargs):
        self.edits.append(kwargs)


class _FakeChannel:
    def __init__(self):
        self.partial_messages: dict[int, _FakePartialMessage] = {}

    def get_partial_message(self, message_id: int):
        message = self.partial_messages.get(message_id)
        if message is None:
            message = _FakePartialMessage(message_id)
            self.partial_messages[message_id] = message
        return message


class _FakeUser:
    def __init__(self, user_id: int = 123, *, dm_forbidden: bool = False):
        self.id = user_id
        self.display_name = f"user-{user_id}"
        self.sent_messages: list[dict] = []
        self.dm_forbidden = dm_forbidden

    async def send(self, **kwargs):
        if self.dm_forbidden:
            response = SimpleNamespace(status=403, reason="Forbidden")
            raise discord.Forbidden(response, "Forbidden")
        self.sent_messages.append(kwargs)


class _FakeInteraction:
    def __init__(
        self,
        *,
        user: _FakeUser | None = None,
        channel: _FakeChannel | None = None,
        message_id: int | None = None,
        client=None,
    ):
        self.guild = SimpleNamespace(id=1)
        self.user = user or _FakeUser()
        self.channel = channel or _FakeChannel()
        self.message = SimpleNamespace(id=message_id) if message_id is not None else None
        self.response = _FakeResponse()
        self.followup = _FakeFollowup()
        self.client = client


class _FakeRegistrationService:
    def __init__(self):
        self.sub_calls: list[dict] = []
        self.domme_calls: list[dict] = []
        self.domme_delay_seconds = 0.0

    async def register_sub(self, **kwargs):
        self.sub_calls.append(kwargs)
        return SimpleNamespace(sub=SimpleNamespace(send_name=kwargs["send_names"][0]), send_names=tuple(kwargs["send_names"]))

    async def register_domme(self, **kwargs):
        if self.domme_delay_seconds > 0:
            await asyncio.sleep(self.domme_delay_seconds)
        self.domme_calls.append(kwargs)
        return SimpleNamespace(
            creator=SimpleNamespace(id=99),
            webhook_url="https://example.com/webhook",
        )


class _FakeBot:
    def __init__(self, settings, *, maintenance_enabled: bool = False):
        self.guild_settings_repo = SimpleNamespace(get=self._get_settings)
        self.registration_service = _FakeRegistrationService()
        self._settings = settings
        self._maintenance_enabled = maintenance_enabled
        self.maintenance_service = SimpleNamespace(
            registrations_blocked=self._registrations_blocked
        )

    async def _get_settings(self, guild_id: int):
        del guild_id
        return self._settings

    async def _registrations_blocked(self):
        return self._maintenance_enabled


def test_register_commands_do_not_take_slash_options():
    domme_params = list(inspect.signature(RegistrationCog.register_domme.callback).parameters)
    sub_params = list(inspect.signature(RegistrationCog.register_sub.callback).parameters)
    assert domme_params == ["self", "interaction"]
    assert sub_params == ["self", "interaction"]


def test_register_domme_denied_when_role_missing_from_config(monkeypatch):
    monkeypatch.setattr("rob.discord.cogs.registration.member_has_role", lambda *_args, **_kwargs: True)
    interaction = _FakeInteraction()
    bot = _FakeBot(SimpleNamespace(domme_role_id=None, sub_role_id=22, send_track_channel_id=77))
    cog = RegistrationCog(bot)

    asyncio.run(RegistrationCog.register_domme.callback(cog, interaction))

    assert interaction.response.messages[0]["ephemeral"] is True
    assert interaction.response.messages[0]["view"] is not None
    assert bot.registration_service.domme_calls == []


def test_register_domme_denied_when_member_lacks_configured_role(monkeypatch):
    monkeypatch.setattr("rob.discord.cogs.registration.member_has_role", lambda *_args, **_kwargs: False)
    interaction = _FakeInteraction()
    bot = _FakeBot(SimpleNamespace(domme_role_id=11, sub_role_id=22, send_track_channel_id=77))
    cog = RegistrationCog(bot)

    asyncio.run(RegistrationCog.register_domme.callback(cog, interaction))

    assert interaction.response.messages[0]["ephemeral"] is True
    assert interaction.response.messages[0]["view"] is not None
    assert bot.registration_service.domme_calls == []


def test_register_domme_allowed_sends_dm_setup_flow(monkeypatch):
    monkeypatch.setattr("rob.discord.cogs.registration.member_has_role", lambda *_args, **_kwargs: True)
    interaction = _FakeInteraction()
    bot = _FakeBot(SimpleNamespace(domme_role_id=11, sub_role_id=22, send_track_channel_id=77))
    cog = RegistrationCog(bot)

    asyncio.run(RegistrationCog.register_domme.callback(cog, interaction))

    assert len(interaction.user.sent_messages) == 1
    dm_view = interaction.user.sent_messages[0]["view"]
    assert type(dm_view.children[1]).__name__ == "ActionRow"
    assert dm_view.children[1].children[0].label == "Continue Setup"
    assert interaction.response.messages[0]["ephemeral"] is True
    assert bot.registration_service.domme_calls == []


def test_register_domme_blocked_during_maintenance(monkeypatch):
    monkeypatch.setattr("rob.discord.cogs.registration.member_has_role", lambda *_args, **_kwargs: True)
    interaction = _FakeInteraction()
    bot = _FakeBot(
        SimpleNamespace(domme_role_id=11, sub_role_id=22, send_track_channel_id=77),
        maintenance_enabled=True,
    )
    cog = RegistrationCog(bot)

    asyncio.run(RegistrationCog.register_domme.callback(cog, interaction))

    assert interaction.user.sent_messages == []
    assert interaction.response.messages[0]["ephemeral"] is True
    assert "maintenance" in _view_text(interaction.response.messages[0]).lower()


def _view_text(payload: dict) -> str:
    view = payload.get("view")
    if view is None:
        return str(payload.get("content", ""))
    chunks: list[str] = []
    for top_level in view.children:
        for child in getattr(top_level, "children", []):
            content = getattr(child, "content", None)
            if content:
                chunks.append(str(content))
    return "\n".join(chunks)


def test_domme_setup_button_opens_modal_and_submit_registers_once(monkeypatch):
    monkeypatch.setattr("rob.discord.cogs.registration.member_has_role", lambda *_args, **_kwargs: True)
    user = _FakeUser(user_id=123)
    interaction = _FakeInteraction(user=user)
    bot = _FakeBot(SimpleNamespace(domme_role_id=11, sub_role_id=22, send_track_channel_id=77))
    cog = RegistrationCog(bot)

    asyncio.run(RegistrationCog.register_domme.callback(cog, interaction))
    dm_payload = user.sent_messages[0]
    start_button = dm_payload["view"].children[1].children[0]

    channel = _FakeChannel()
    dm_click_interaction = _FakeInteraction(user=user, channel=channel, message_id=555)
    asyncio.run(start_button.callback(dm_click_interaction))
    modal = dm_click_interaction.response.modal
    assert modal is not None
    modal.throne._value = "missadore"  # noqa: SLF001

    submit_interaction = _FakeInteraction(user=user, channel=channel)
    asyncio.run(modal.on_submit(submit_interaction))

    assert len(bot.registration_service.domme_calls) == 1
    setup_message = channel.partial_messages[555]
    assert len(setup_message.edits) == 1
    assert "Throne Tracking Setup!" in _view_text(setup_message.edits[0])
    assert submit_interaction.followup.messages == []


def test_domme_webhook_success_edits_same_message_without_followup_spam():
    user = _FakeUser(user_id=123)
    channel = _FakeChannel()
    bot = _FakeBot(SimpleNamespace(domme_role_id=11, sub_role_id=22, send_track_channel_id=77))
    async def _get_domme(_domme_id: int):
        return SimpleNamespace(webhook_connected_at=True, last_successful_event_at=None)

    bot.dommes_repo = SimpleNamespace(get=_get_domme)
    interaction = _FakeInteraction(user=user, channel=channel, message_id=555, client=bot)
    yes_button = YesButton(domme_id=99, send_track_channel_id=77)

    asyncio.run(yes_button.callback(interaction))

    assert interaction.followup.messages == []
    assert interaction.response.edits
    assert "What Rob collects" in _view_text(interaction.response.edits[0])


def test_domme_modal_double_submit_is_guarded(monkeypatch):
    monkeypatch.setattr("rob.discord.cogs.registration.member_has_role", lambda *_args, **_kwargs: True)
    user = _FakeUser(user_id=123)
    interaction = _FakeInteraction(user=user)
    bot = _FakeBot(SimpleNamespace(domme_role_id=11, sub_role_id=22, send_track_channel_id=77))
    bot.registration_service.domme_delay_seconds = 0.05
    cog = RegistrationCog(bot)

    asyncio.run(RegistrationCog.register_domme.callback(cog, interaction))
    start_button = user.sent_messages[0]["view"].children[1].children[0]
    channel = _FakeChannel()
    dm_click_interaction = _FakeInteraction(user=user, channel=channel, message_id=555)
    asyncio.run(start_button.callback(dm_click_interaction))
    modal = dm_click_interaction.response.modal
    assert modal is not None
    modal.throne._value = "missadore"  # noqa: SLF001

    first_submit = _FakeInteraction(user=user, channel=channel)
    second_submit = _FakeInteraction(user=user, channel=channel)

    async def _submit_twice():
        first_task = asyncio.create_task(modal.on_submit(first_submit))
        await asyncio.sleep(0.005)
        second_task = asyncio.create_task(modal.on_submit(second_submit))
        await asyncio.gather(first_task, second_task)

    asyncio.run(_submit_twice())

    assert len(bot.registration_service.domme_calls) == 1
    assert second_submit.response.messages
    second_text = _view_text(second_submit.response.messages[0])
    assert "already being processed" in second_text


def test_register_domme_handles_blocked_dms_with_one_ephemeral_error(monkeypatch):
    monkeypatch.setattr("rob.discord.cogs.registration.member_has_role", lambda *_args, **_kwargs: True)
    interaction = _FakeInteraction(user=_FakeUser(user_id=123, dm_forbidden=True))
    bot = _FakeBot(SimpleNamespace(domme_role_id=11, sub_role_id=22, send_track_channel_id=77))
    cog = RegistrationCog(bot)

    asyncio.run(RegistrationCog.register_domme.callback(cog, interaction))

    assert len(interaction.response.messages) == 1
    assert interaction.response.messages[0]["ephemeral"] is True
    assert "couldn't DM you" in _view_text(interaction.response.messages[0])
    assert bot.registration_service.domme_calls == []


def test_register_sub_allowed_opens_modal(monkeypatch):
    monkeypatch.setattr("rob.discord.cogs.registration.member_has_role", lambda *_args, **_kwargs: True)
    interaction = _FakeInteraction()
    bot = _FakeBot(SimpleNamespace(domme_role_id=11, sub_role_id=22, send_track_channel_id=77))
    cog = RegistrationCog(bot)

    asyncio.run(RegistrationCog.register_sub.callback(cog, interaction))

    assert interaction.response.modal is not None
    assert type(interaction.response.modal).__name__ == "_SubRegistrationModal"
    assert bot.registration_service.sub_calls == []


def test_register_sub_blocked_during_maintenance(monkeypatch):
    monkeypatch.setattr("rob.discord.cogs.registration.member_has_role", lambda *_args, **_kwargs: True)
    interaction = _FakeInteraction()
    bot = _FakeBot(
        SimpleNamespace(domme_role_id=11, sub_role_id=22, send_track_channel_id=77),
        maintenance_enabled=True,
    )
    cog = RegistrationCog(bot)

    asyncio.run(RegistrationCog.register_sub.callback(cog, interaction))

    assert interaction.response.modal is None
    assert interaction.response.messages[0]["ephemeral"] is True
    assert "maintenance" in _view_text(interaction.response.messages[0]).lower()


def test_register_sub_denied_when_role_missing_from_config(monkeypatch):
    monkeypatch.setattr("rob.discord.cogs.registration.member_has_role", lambda *_args, **_kwargs: True)
    interaction = _FakeInteraction()
    bot = _FakeBot(SimpleNamespace(domme_role_id=11, sub_role_id=None, send_track_channel_id=77))
    cog = RegistrationCog(bot)

    asyncio.run(RegistrationCog.register_sub.callback(cog, interaction))

    assert interaction.response.messages[0]["ephemeral"] is True
    assert interaction.response.messages[0]["view"] is not None
    assert bot.registration_service.sub_calls == []


def test_register_sub_denied_when_member_lacks_configured_role(monkeypatch):
    monkeypatch.setattr("rob.discord.cogs.registration.member_has_role", lambda *_args, **_kwargs: False)
    interaction = _FakeInteraction()
    bot = _FakeBot(SimpleNamespace(domme_role_id=11, sub_role_id=22, send_track_channel_id=77))
    cog = RegistrationCog(bot)

    asyncio.run(RegistrationCog.register_sub.callback(cog, interaction))

    assert interaction.response.messages[0]["ephemeral"] is True
    assert interaction.response.messages[0]["view"] is not None
    assert bot.registration_service.sub_calls == []
