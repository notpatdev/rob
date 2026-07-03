"""Wiring tests for the DM-based Dom/me onboarding cog. discord.py is mocked."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from rob.config.guilds import MAIN_GUILD_ID, TEST_GUILD_ID
from rob.discord.cogs.dm_onboarding import (
    DMOnboardingCog,
    _read_prefs_from_interaction,
)
from rob.services.dm_onboarding_service import OnboardingError
from rob.ui.cards.dm_onboarding import (
    ID_MIGRATION_LEADERBOARD_ACCESS,
    ID_PREFS_LEADERBOARD_ACCESS,
    LEADERBOARD_ACCESS_ON_VALUE,
    PreferencesView,
)

# A guild that is neither main nor test: the new onboarding flow must stay off.
OTHER_GUILD_ID = 424242424242424242


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeOnboardingRepo:
    def __init__(self, state=None):
        self.state = state
        self.set_dm_message_calls: list[dict] = []

    async def get(self, *, guild_id, discord_user_id):
        return self.state

    async def set_dm_message(self, **kwargs):
        self.set_dm_message_calls.append(kwargs)
        return self.state


class _FakeMessage:
    def __init__(self, message_id=999, channel_id=42, edit_exception=None):
        self.id = message_id
        self.channel = SimpleNamespace(id=channel_id)
        self.edit_exception = edit_exception
        self.edits: list[dict] = []

    async def edit(self, **kwargs):
        if self.edit_exception is not None:
            raise self.edit_exception
        self.edits.append(kwargs)
        return self


class _FakeDMChannel:
    def __init__(self, message: _FakeMessage):
        self._message = message

    def get_partial_message(self, _message_id):
        return self._message


class _FakeUser:
    def __init__(self, user_id=1, name="Tester", send_exception=None):
        self.id = user_id
        self.name = name
        self.display_name = name
        self.send_exception = send_exception
        self.sent_messages: list[dict] = []
        self.dm_channel = None
        self._last_message = None

    async def send(self, **kwargs):
        if self.send_exception is not None:
            raise self.send_exception
        self.sent_messages.append(kwargs)
        msg = _FakeMessage()
        self._last_message = msg
        return msg

    async def create_dm(self):
        return _FakeDMChannel(self._last_message or _FakeMessage())


class _FakeBot:
    def __init__(self, *, onboarding_state=None, dm_message=None, user=None):
        self.dm_onboarding_service = MagicMock()
        self.dm_onboarding_service.start = AsyncMock()
        self.dm_onboarding_service.submit_throne_input = AsyncMock()
        self.dm_onboarding_service.confirm_identity = AsyncMock(
            return_value="https://example.com/webhook/abc"
        )
        self.dm_onboarding_service.reject_identity = AsyncMock()
        self.dm_onboarding_service.mark_webhook_received = AsyncMock()
        self.dm_onboarding_service.save_preferences = AsyncMock()
        self.dm_onboarding_service.defer_migration = AsyncMock()
        self.domme_onboarding_repo = _FakeOnboardingRepo(state=onboarding_state)
        self.dm_message = dm_message
        self.user_obj = user
        self.added_views: list[discord.ui.View] = []
        self.registration_service = MagicMock()
        self.registration_service.reissue_domme_webhook = AsyncMock(
            return_value=SimpleNamespace(webhook_url="https://example.com/new/url")
        )
        self.registration_service.build_webhook_url = MagicMock(
            return_value="https://example.com/built/url"
        )
        self.dommes_repo = MagicMock()
        self.dommes_repo.set_preferences = AsyncMock()
        self.dommes_repo.get_by_user_id = AsyncMock(
            return_value=SimpleNamespace(
                webhook_secret="secret",
                throne_creator_id="cid",
            )
        )

    def get_user(self, _user_id):
        return self.user_obj

    async def fetch_user(self, _user_id):
        return self.user_obj

    def add_view(self, view):
        self.added_views.append(view)

    def get_cog(self, _name):
        return None


def _make_interaction(
    *,
    user_id=1,
    guild_id=None,
    message=None,
    response=None,
    followup=None,
    user_name="Tester",
):
    response = response or MagicMock()
    response.send_message = AsyncMock()
    response.defer = AsyncMock()
    response.send_modal = AsyncMock()
    response.edit_message = AsyncMock()
    followup = followup or MagicMock()
    followup.send = AsyncMock()
    user = _FakeUser(user_id=user_id, name=user_name)
    return SimpleNamespace(
        user=user,
        guild_id=guild_id,
        message=message,
        response=response,
        followup=followup,
        channel=None,
        view=None,
    )


# ---------------------------------------------------------------------------
# start_onboarding_dm
# ---------------------------------------------------------------------------


def test_start_onboarding_dm_outside_new_system_guild_returns_error():
    bot = _FakeBot()
    cog = DMOnboardingCog(bot)
    user = _FakeUser()
    ok, message, err = asyncio.run(
        cog.start_onboarding_dm(user=user, guild_id=OTHER_GUILD_ID)
    )
    assert ok is False
    assert message is None
    assert err is not None
    bot.dm_onboarding_service.start.assert_not_awaited()


def test_start_onboarding_dm_main_guild_sends_intro():
    bot = _FakeBot()
    cog = DMOnboardingCog(bot)
    user = _FakeUser(user_id=7, name="Aria")
    ok, _message, err = asyncio.run(
        cog.start_onboarding_dm(user=user, guild_id=MAIN_GUILD_ID)
    )
    assert ok is True
    assert err is None
    bot.dm_onboarding_service.start.assert_awaited_once_with(
        guild_id=MAIN_GUILD_ID, discord_user_id=7
    )
    assert len(user.sent_messages) == 1


def test_start_onboarding_dm_test_guild_sends_intro_and_stores_message():
    bot = _FakeBot()
    cog = DMOnboardingCog(bot)
    user = _FakeUser(user_id=7, name="Aria")
    ok, message, err = asyncio.run(
        cog.start_onboarding_dm(user=user, guild_id=TEST_GUILD_ID)
    )
    assert ok is True
    assert err is None
    bot.dm_onboarding_service.start.assert_awaited_once_with(
        guild_id=TEST_GUILD_ID, discord_user_id=7
    )
    assert len(user.sent_messages) == 1
    assert bot.domme_onboarding_repo.set_dm_message_calls
    call = bot.domme_onboarding_repo.set_dm_message_calls[0]
    assert call["guild_id"] == TEST_GUILD_ID
    assert call["discord_user_id"] == 7
    assert call["dm_message_id"] == message.id
    assert call["dm_channel_id"] == message.channel.id


def test_start_onboarding_dm_handles_forbidden():
    bot = _FakeBot()
    cog = DMOnboardingCog(bot)
    user = _FakeUser(send_exception=discord.Forbidden(MagicMock(status=403), "blocked"))
    ok, message, err = asyncio.run(
        cog.start_onboarding_dm(user=user, guild_id=TEST_GUILD_ID)
    )
    assert ok is False
    assert message is None
    assert "DM" in err


# ---------------------------------------------------------------------------
# Modal open / submit
# ---------------------------------------------------------------------------


def test_handle_open_modal_in_test_guild_sends_modal():
    bot = _FakeBot()
    cog = DMOnboardingCog(bot)
    interaction = _make_interaction(guild_id=TEST_GUILD_ID)
    asyncio.run(cog.handle_open_modal(interaction))
    interaction.response.send_modal.assert_awaited_once()


def test_handle_open_modal_uses_stored_guild_id_in_dm_context():
    bot = _FakeBot(
        onboarding_state=SimpleNamespace(
            guild_id=TEST_GUILD_ID,
            stage="awaiting_throne_input",
            dm_channel_id=1,
            dm_message_id=2,
        )
    )
    cog = DMOnboardingCog(bot)
    interaction = _make_interaction(guild_id=None)
    asyncio.run(cog.handle_open_modal(interaction))
    interaction.response.send_modal.assert_awaited_once()


def test_handle_open_modal_in_main_guild_sends_modal():
    bot = _FakeBot()
    cog = DMOnboardingCog(bot)
    interaction = _make_interaction(guild_id=MAIN_GUILD_ID)
    asyncio.run(cog.handle_open_modal(interaction))
    interaction.response.send_modal.assert_awaited_once()


def test_handle_open_modal_outside_new_system_guild_short_circuits():
    bot = _FakeBot()
    cog = DMOnboardingCog(bot)
    interaction = _make_interaction(guild_id=OTHER_GUILD_ID)
    asyncio.run(cog.handle_open_modal(interaction))
    interaction.response.send_modal.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()


def test_handle_modal_submit_edits_stored_dm_with_identity_card():
    dm_message = _FakeMessage(message_id=111, channel_id=222)
    user = _FakeUser(user_id=42)
    bot = _FakeBot(
        onboarding_state=SimpleNamespace(
            guild_id=TEST_GUILD_ID,
            stage="awaiting_throne_input",
            dm_channel_id=222,
            dm_message_id=111,
        ),
        dm_message=dm_message,
        user=user,
    )
    user._last_message = dm_message
    user.dm_channel = _FakeDMChannel(dm_message)
    bot.dm_onboarding_service.submit_throne_input.return_value = SimpleNamespace(
        throne_handle="aria", throne_display_name="Aria"
    )
    cog = DMOnboardingCog(bot)
    interaction = _make_interaction(user_id=42, guild_id=TEST_GUILD_ID)

    asyncio.run(
        cog.handle_modal_submit(
            interaction, guild_id=TEST_GUILD_ID, throne_input="aria"
        )
    )

    bot.dm_onboarding_service.submit_throne_input.assert_awaited_once_with(
        guild_id=TEST_GUILD_ID, discord_user_id=42, throne_input="aria"
    )
    assert dm_message.edits, "stored DM message should be edited with identity card"


def test_handle_modal_submit_returns_error_card_on_resolution_failure():
    dm_message = _FakeMessage()
    user = _FakeUser(user_id=42)
    bot = _FakeBot(
        onboarding_state=SimpleNamespace(
            guild_id=TEST_GUILD_ID,
            stage="awaiting_throne_input",
            dm_channel_id=222,
            dm_message_id=111,
        ),
        user=user,
    )
    user._last_message = dm_message
    bot.dm_onboarding_service.submit_throne_input.side_effect = OnboardingError(
        "couldn't resolve"
    )
    cog = DMOnboardingCog(bot)
    interaction = _make_interaction(user_id=42, guild_id=TEST_GUILD_ID)
    asyncio.run(
        cog.handle_modal_submit(
            interaction, guild_id=TEST_GUILD_ID, throne_input="bad"
        )
    )
    assert dm_message.edits, "error card should be rendered into the stored DM"


# ---------------------------------------------------------------------------
# identity confirm + reject
# ---------------------------------------------------------------------------


def test_handle_identity_yes_advances_to_webhook_setup():
    dm_message = _FakeMessage()
    user = _FakeUser(user_id=42)
    bot = _FakeBot(
        onboarding_state=SimpleNamespace(
            guild_id=TEST_GUILD_ID,
            stage="awaiting_identity_confirm",
            dm_channel_id=222,
            dm_message_id=111,
        ),
        user=user,
    )
    user._last_message = dm_message
    cog = DMOnboardingCog(bot)
    interaction = _make_interaction(user_id=42)
    asyncio.run(cog.handle_identity_yes(interaction))
    bot.dm_onboarding_service.confirm_identity.assert_awaited_once()
    assert dm_message.edits, "webhook setup card should be edited into stored DM"


def test_handle_identity_no_returns_to_intro_card():
    dm_message = _FakeMessage()
    user = _FakeUser(user_id=42)
    bot = _FakeBot(
        onboarding_state=SimpleNamespace(
            guild_id=TEST_GUILD_ID,
            stage="awaiting_identity_confirm",
            dm_channel_id=222,
            dm_message_id=111,
        ),
        user=user,
    )
    user._last_message = dm_message
    cog = DMOnboardingCog(bot)
    interaction = _make_interaction(user_id=42)
    asyncio.run(cog.handle_identity_no(interaction))
    bot.dm_onboarding_service.reject_identity.assert_awaited_once()
    assert dm_message.edits, "intro card should be re-rendered into stored DM"


# ---------------------------------------------------------------------------
# Webhook retry rotates URL and refreshes the same DM
# ---------------------------------------------------------------------------


def test_handle_webhook_retry_rotates_and_refreshes_dm():
    dm_message = _FakeMessage()
    user = _FakeUser(user_id=42)
    bot = _FakeBot(
        onboarding_state=SimpleNamespace(
            guild_id=TEST_GUILD_ID,
            stage="awaiting_webhook",
            dm_channel_id=222,
            dm_message_id=111,
        ),
        user=user,
    )
    user._last_message = dm_message
    cog = DMOnboardingCog(bot)
    interaction = _make_interaction(user_id=42)
    asyncio.run(cog.handle_webhook_retry(interaction))
    bot.registration_service.reissue_domme_webhook.assert_awaited_once_with(
        guild_id=TEST_GUILD_ID, discord_user_id=42
    )
    assert dm_message.edits, "refreshed webhook card should be edited into stored DM"


# ---------------------------------------------------------------------------
# Save preferences => success card + persistence + onboarding complete
# ---------------------------------------------------------------------------


def test_handle_save_preferences_persists_and_renders_success_card():
    dm_message = _FakeMessage()
    user = _FakeUser(user_id=42)
    bot = _FakeBot(
        onboarding_state=SimpleNamespace(
            guild_id=TEST_GUILD_ID,
            stage="awaiting_preferences",
            dm_channel_id=222,
            dm_message_id=111,
        ),
        user=user,
    )
    user._last_message = dm_message
    select_access = SimpleNamespace(
        custom_id=ID_PREFS_LEADERBOARD_ACCESS, values=[LEADERBOARD_ACCESS_ON_VALUE]
    )
    fake_components = [
        SimpleNamespace(children=[
            SimpleNamespace(children=[select_access]),
        ]),
    ]
    fake_message = SimpleNamespace(components=fake_components)
    cog = DMOnboardingCog(bot)
    interaction = _make_interaction(user_id=42, message=fake_message)

    asyncio.run(cog.handle_save_preferences(interaction))

    bot.dm_onboarding_service.save_preferences.assert_awaited_once_with(
        guild_id=TEST_GUILD_ID,
        discord_user_id=42,
    )
    assert dm_message.edits, "success card should be edited into stored DM"


def test_handle_save_preferences_grants_access_from_live_view(monkeypatch):
    # Regression: the handler must read the live view, not the message
    # components, which only carry the default ("off").
    user = _FakeUser(user_id=42)
    bot = _FakeBot(
        onboarding_state=SimpleNamespace(
            guild_id=TEST_GUILD_ID,
            stage="awaiting_preferences",
            dm_channel_id=222,
            dm_message_id=111,
        ),
        user=user,
    )
    user._last_message = _FakeMessage()

    applied: dict = {}

    async def _fake_apply(_bot, *, guild_id, user_id, enabled):
        applied["enabled"] = enabled
        return True

    monkeypatch.setattr(
        "rob.discord.cogs.dm_onboarding.apply_leaderboard_access", _fake_apply
    )

    # The live view says "on"; the message components still say "off".
    view = PreferencesView(default_leaderboard_access=False)
    view.leaderboard_access_select._values = [LEADERBOARD_ACCESS_ON_VALUE]

    cog = DMOnboardingCog(bot)
    interaction = _make_interaction(
        user_id=42, message=SimpleNamespace(components=[])
    )

    asyncio.run(cog.handle_save_preferences(interaction, view=view))

    assert applied.get("enabled") is True


# ---------------------------------------------------------------------------
# on_throne_test_webhook_received auto-advance
# ---------------------------------------------------------------------------


def test_on_throne_test_webhook_received_advances_dm_to_preferences():
    dm_message = _FakeMessage()
    user = _FakeUser(user_id=42)
    bot = _FakeBot(
        onboarding_state=SimpleNamespace(
            guild_id=TEST_GUILD_ID,
            stage="awaiting_webhook",
            dm_channel_id=222,
            dm_message_id=111,
        ),
        user=user,
    )
    user._last_message = dm_message
    cog = DMOnboardingCog(bot)
    advanced = asyncio.run(
        cog.on_throne_test_webhook_received(
            guild_id=TEST_GUILD_ID, discord_user_id=42
        )
    )
    assert advanced is True
    bot.dm_onboarding_service.mark_webhook_received.assert_awaited_once_with(
        guild_id=TEST_GUILD_ID, discord_user_id=42
    )
    assert dm_message.edits, "preferences card should be edited into stored DM"


def test_on_throne_test_webhook_received_outside_new_system_guild_is_noop():
    # A live onboarding row exists, so the guild gate (not the missing-state
    # path) must short-circuit the auto-advance.
    bot = _FakeBot(
        onboarding_state=SimpleNamespace(
            guild_id=OTHER_GUILD_ID,
            stage="awaiting_webhook",
            dm_channel_id=222,
            dm_message_id=111,
        )
    )
    cog = DMOnboardingCog(bot)
    advanced = asyncio.run(
        cog.on_throne_test_webhook_received(
            guild_id=OTHER_GUILD_ID, discord_user_id=42
        )
    )
    assert advanced is False
    bot.dm_onboarding_service.mark_webhook_received.assert_not_awaited()


def test_on_throne_test_webhook_received_advances_dm_in_main_guild():
    dm_message = _FakeMessage()
    user = _FakeUser(user_id=42)
    bot = _FakeBot(
        onboarding_state=SimpleNamespace(
            guild_id=MAIN_GUILD_ID,
            stage="awaiting_webhook",
            dm_channel_id=222,
            dm_message_id=111,
        ),
        user=user,
    )
    user._last_message = dm_message
    cog = DMOnboardingCog(bot)
    advanced = asyncio.run(
        cog.on_throne_test_webhook_received(
            guild_id=MAIN_GUILD_ID, discord_user_id=42
        )
    )
    assert advanced is True
    bot.dm_onboarding_service.mark_webhook_received.assert_awaited_once_with(
        guild_id=MAIN_GUILD_ID, discord_user_id=42
    )


def test_on_throne_test_webhook_received_handles_completed_state():
    bot = _FakeBot(
        onboarding_state=SimpleNamespace(
            guild_id=TEST_GUILD_ID,
            stage="completed",
            dm_channel_id=222,
            dm_message_id=111,
        )
    )
    cog = DMOnboardingCog(bot)
    advanced = asyncio.run(
        cog.on_throne_test_webhook_received(
            guild_id=TEST_GUILD_ID, discord_user_id=42
        )
    )
    assert advanced is False
    bot.dm_onboarding_service.mark_webhook_received.assert_not_awaited()


# ---------------------------------------------------------------------------
# Missing/deleted DM
# ---------------------------------------------------------------------------


def test_missing_dm_during_edit_does_not_raise():
    user = _FakeUser(user_id=42)
    bot = _FakeBot(
        onboarding_state=SimpleNamespace(
            guild_id=TEST_GUILD_ID,
            stage="awaiting_preferences",
            dm_channel_id=222,
            dm_message_id=111,
        ),
        user=user,
    )

    class _DeadDMChannel:
        def get_partial_message(self, _id):
            msg = _FakeMessage()
            msg.edit_exception = discord.NotFound(MagicMock(status=404), "gone")
            return msg

    user.dm_channel = _DeadDMChannel()
    cog = DMOnboardingCog(bot)
    from rob.ui.cards.dm_onboarding import success_card

    ok = asyncio.run(
        cog._edit_stored_dm(
            guild_id=TEST_GUILD_ID,
            discord_user_id=42,
            rendered=success_card(leaderboard_access=True),
        )
    )
    assert ok is False


# ---------------------------------------------------------------------------
# Migration defer + save
# ---------------------------------------------------------------------------


def test_handle_migration_defer_calls_service():
    bot = _FakeBot(
        onboarding_state=SimpleNamespace(
            guild_id=TEST_GUILD_ID,
            stage="awaiting_preferences",
            dm_channel_id=1,
            dm_message_id=2,
        )
    )
    cog = DMOnboardingCog(bot)
    interaction = _make_interaction(user_id=42)
    asyncio.run(cog.handle_migration_defer(interaction))
    bot.dm_onboarding_service.defer_migration.assert_awaited_once_with(
        guild_id=TEST_GUILD_ID, discord_user_id=42, days=7
    )


def test_handle_migration_save_persists_preferences():
    bot = _FakeBot(
        onboarding_state=SimpleNamespace(
            guild_id=TEST_GUILD_ID,
            stage="awaiting_preferences",
            dm_channel_id=1,
            dm_message_id=2,
        )
    )
    cog = DMOnboardingCog(bot)
    select_access = SimpleNamespace(
        custom_id=ID_MIGRATION_LEADERBOARD_ACCESS, values=[LEADERBOARD_ACCESS_ON_VALUE]
    )
    fake_components = [
        SimpleNamespace(children=[
            SimpleNamespace(children=[select_access]),
        ]),
    ]
    fake_message = MagicMock()
    fake_message.components = fake_components
    fake_message.edit = AsyncMock()
    interaction = _make_interaction(user_id=42, message=fake_message)
    asyncio.run(cog.handle_migration_save(interaction))
    bot.dommes_repo.set_preferences.assert_awaited_once()
    kwargs = bot.dommes_repo.set_preferences.await_args.kwargs
    assert kwargs["confirm"] is True
    assert kwargs["clear_defer"] is True


# ---------------------------------------------------------------------------
# /register domme routing
# ---------------------------------------------------------------------------


def test_register_domme_routes_to_dm_cog_in_test_guild():
    from unittest.mock import patch

    from rob.discord.cogs.registration import RegistrationCog

    bot = MagicMock()
    bot.maintenance_service = MagicMock()
    bot.maintenance_service.registrations_blocked = AsyncMock(return_value=False)
    bot.guild_settings_repo = MagicMock()
    bot.guild_settings_repo.get = AsyncMock(
        return_value=SimpleNamespace(
            domme_role_id=10, send_track_channel_id=99
        )
    )
    dm_cog = MagicMock()
    dm_cog.start_onboarding_dm = AsyncMock(return_value=(True, _FakeMessage(), None))
    bot.get_cog = MagicMock(return_value=dm_cog)

    cog = RegistrationCog(bot)

    member = MagicMock()
    member.id = 42
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=TEST_GUILD_ID),
        user=member,
        response=MagicMock(),
    )
    interaction.response.send_message = AsyncMock()

    with patch(
        "rob.discord.cogs.registration.member_has_role", return_value=True
    ):
        asyncio.run(cog.register_domme.callback(cog, interaction))

    dm_cog.start_onboarding_dm.assert_awaited_once_with(
        user=member, guild_id=TEST_GUILD_ID
    )
    interaction.response.send_message.assert_awaited_once()


def test_register_domme_routes_to_dm_cog_in_main_guild():
    from unittest.mock import patch

    from rob.discord.cogs.registration import RegistrationCog

    bot = MagicMock()
    bot.maintenance_service = MagicMock()
    bot.maintenance_service.registrations_blocked = AsyncMock(return_value=False)
    bot.guild_settings_repo = MagicMock()
    bot.guild_settings_repo.get = AsyncMock(
        return_value=SimpleNamespace(
            domme_role_id=10, send_track_channel_id=99
        )
    )
    dm_cog = MagicMock()
    dm_cog.start_onboarding_dm = AsyncMock(return_value=(True, _FakeMessage(), None))
    bot.get_cog = MagicMock(return_value=dm_cog)

    cog = RegistrationCog(bot)
    member = MagicMock()
    member.id = 42
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=MAIN_GUILD_ID),
        user=member,
        response=MagicMock(),
    )
    interaction.response.send_message = AsyncMock()

    with patch(
        "rob.discord.cogs.registration.member_has_role", return_value=True
    ):
        asyncio.run(cog.register_domme.callback(cog, interaction))

    dm_cog.start_onboarding_dm.assert_awaited_once_with(
        user=member, guild_id=MAIN_GUILD_ID
    )
    interaction.response.send_message.assert_awaited_once()


def test_register_domme_uses_legacy_flow_outside_new_system_guild():
    from unittest.mock import patch

    from rob.discord.cogs.registration import RegistrationCog

    bot = MagicMock()
    bot.maintenance_service = MagicMock()
    bot.maintenance_service.registrations_blocked = AsyncMock(return_value=False)
    bot.guild_settings_repo = MagicMock()
    bot.guild_settings_repo.get = AsyncMock(
        return_value=SimpleNamespace(
            domme_role_id=10, send_track_channel_id=99
        )
    )
    dm_cog = MagicMock()
    dm_cog.start_onboarding_dm = AsyncMock()
    bot.get_cog = MagicMock(return_value=dm_cog)

    cog = RegistrationCog(bot)
    member = MagicMock()
    member.id = 42
    member.send = AsyncMock()
    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=OTHER_GUILD_ID),
        user=member,
        response=MagicMock(),
    )
    interaction.response.send_message = AsyncMock()

    with patch(
        "rob.discord.cogs.registration.member_has_role", return_value=True
    ):
        asyncio.run(cog.register_domme.callback(cog, interaction))

    dm_cog.start_onboarding_dm.assert_not_awaited()
    interaction.response.send_message.assert_awaited_once()


# ---------------------------------------------------------------------------
# _read_prefs_from_interaction helper
# ---------------------------------------------------------------------------


def test_read_prefs_from_interaction_defaults_when_no_data():
    interaction = SimpleNamespace(view=None, message=None)
    assert _read_prefs_from_interaction(interaction) is False


def test_read_prefs_from_interaction_parses_components_when_view_missing():
    select_access = SimpleNamespace(
        custom_id=ID_PREFS_LEADERBOARD_ACCESS, values=[LEADERBOARD_ACCESS_ON_VALUE]
    )
    components = [
        SimpleNamespace(children=[select_access]),
    ]
    interaction = SimpleNamespace(
        view=None, message=SimpleNamespace(components=components)
    )
    assert _read_prefs_from_interaction(interaction) is True


# ---------------------------------------------------------------------------
# notify_bot_onboarding_webhook_verified URL derivation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "notify_url",
    [
        "http://127.0.0.1:8090/sends/process",
        "https://rob.example.com:9000/ops/sends/process",
    ],
)
def test_notify_bot_onboarding_webhook_verified_derives_endpoint(notify_url):
    from rob.services.bot_notify_client import notify_bot_onboarding_webhook_verified
    from urllib.parse import urlsplit

    import rob.services.bot_notify_client as mod
    from unittest.mock import patch

    class _Resp:
        status = 200

        async def text(self):
            return ""

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    posted = {}

    class _Session:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def post(self, endpoint, *, json, headers):
            posted["endpoint"] = endpoint
            posted["json"] = json
            return _Resp()

    with patch.object(mod, "ClientSession", _Session):
        result = asyncio.run(
            notify_bot_onboarding_webhook_verified(
                notify_base_url=notify_url,
                secret="topsecret",
                guild_id=TEST_GUILD_ID,
                discord_user_id=42,
            )
        )

    assert result is True
    derived = urlsplit(posted["endpoint"])
    assert derived.path == "/onboarding/webhook_verified"
    assert posted["json"] == {"guild_id": TEST_GUILD_ID, "discord_user_id": 42}
