"""DM onboarding service state-machine tests."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rob.config.guilds import MAIN_GUILD_ID, TEST_GUILD_ID
from rob.database.repositories.domme_onboarding import (
    STAGE_AWAITING_PREFERENCES,
    STAGE_AWAITING_THRONE_INPUT,
    STAGE_AWAITING_WEBHOOK_TEST,
    STAGE_COMPLETE,
    STAGE_CONFIRMING_IDENTITY,
)
from rob.services.dm_onboarding_service import (
    DMOnboardingService,
    OnboardingError,
)

# A guild id that is neither the main nor the test guild: the new system must
# stay disabled there.
OTHER_GUILD_ID = 424242424242424242


class _FakeOnboardingRepo:
    def __init__(self):
        self.started = []
        self.stage_calls = []
        self.pending_throne_calls = []
        self.complete_calls = []
        self.state = None

    async def start(self, *, guild_id, discord_user_id, dm_channel_id=None, dm_message_id=None):
        self.started.append((guild_id, discord_user_id))

    async def set_stage(self, *, guild_id, discord_user_id, stage):
        self.stage_calls.append((guild_id, discord_user_id, stage))

    async def set_pending_throne(self, *, guild_id, discord_user_id, throne_input=None, throne_handle=None, throne_creator_id=None):
        self.pending_throne_calls.append(
            (guild_id, discord_user_id, throne_input, throne_handle, throne_creator_id)
        )

    async def get(self, *, guild_id, discord_user_id):
        return self.state

    async def complete(self, *, guild_id, discord_user_id):
        self.complete_calls.append((guild_id, discord_user_id))


class _FakeDommesRepo:
    def __init__(self, domme=None):
        self.domme = domme
        self.set_preferences_calls = []
        self.defer_preferences_calls = []

    async def get_by_user_id(self, _guild_id, _discord_user_id):
        return self.domme

    async def set_preferences(self, **kwargs):
        self.set_preferences_calls.append(kwargs)
        return self.domme

    async def defer_preferences(self, **kwargs):
        self.defer_preferences_calls.append(kwargs)
        return self.domme


def _service(*, onboarding=None, dommes=None, throne=None, registration=None):
    return DMOnboardingService(
        onboarding=onboarding or _FakeOnboardingRepo(),
        dommes=dommes or _FakeDommesRepo(),
        throne=throne or SimpleNamespace(),
        registration=registration or SimpleNamespace(),
    )


def test_onboarding_enabled_for_main_and_test_guild():
    # The new system is now live on both main and test.
    assert DMOnboardingService.is_enabled_for(TEST_GUILD_ID) is True
    assert DMOnboardingService.is_enabled_for(MAIN_GUILD_ID) is True
    # ...but stays disabled for unrelated guilds and missing ids.
    assert DMOnboardingService.is_enabled_for(OTHER_GUILD_ID) is False
    assert DMOnboardingService.is_enabled_for(None) is False


def test_start_in_main_guild_creates_onboarding_state():
    onboarding = _FakeOnboardingRepo()
    service = _service(onboarding=onboarding)
    asyncio.run(service.start(guild_id=MAIN_GUILD_ID, discord_user_id=1))
    assert onboarding.started == [(MAIN_GUILD_ID, 1)]


def test_start_outside_new_system_guild_raises():
    service = _service()
    with pytest.raises(OnboardingError):
        asyncio.run(service.start(guild_id=OTHER_GUILD_ID, discord_user_id=1))


def test_start_creates_onboarding_state():
    onboarding = _FakeOnboardingRepo()
    service = _service(onboarding=onboarding)
    asyncio.run(service.start(guild_id=TEST_GUILD_ID, discord_user_id=42))
    assert onboarding.started == [(TEST_GUILD_ID, 42)]
    assert onboarding.stage_calls == [(TEST_GUILD_ID, 42, STAGE_AWAITING_THRONE_INPUT)]


def test_submit_throne_input_resolves_and_advances_stage():
    onboarding = _FakeOnboardingRepo()
    throne = SimpleNamespace(
        resolve_creator=AsyncMock(
            return_value=SimpleNamespace(
                creator_id="cid-1",
                throne_handle="cool",
                hide_own_purchases=False,
            )
        )
    )
    service = _service(onboarding=onboarding, throne=throne)

    identity = asyncio.run(
        service.submit_throne_input(
            guild_id=TEST_GUILD_ID,
            discord_user_id=42,
            throne_input="https://throne.com/cool",
        )
    )

    throne.resolve_creator.assert_awaited_once()
    assert identity.throne_handle == "cool"
    assert identity.creator_id == "cid-1"
    assert onboarding.pending_throne_calls[0][0:2] == (TEST_GUILD_ID, 42)
    assert onboarding.pending_throne_calls[0][3] == "cool"  # throne_handle
    assert onboarding.pending_throne_calls[0][4] == "cid-1"  # creator_id
    assert onboarding.stage_calls == [(TEST_GUILD_ID, 42, STAGE_CONFIRMING_IDENTITY)]


def test_submit_throne_input_rejects_unresolvable():
    throne = SimpleNamespace(resolve_creator=AsyncMock(return_value=None))
    service = _service(throne=throne)
    with pytest.raises(OnboardingError):
        asyncio.run(
            service.submit_throne_input(
                guild_id=TEST_GUILD_ID,
                discord_user_id=42,
                throne_input="cool",
            )
        )


def test_confirm_identity_registers_and_advances_to_webhook():
    onboarding = _FakeOnboardingRepo()
    onboarding.state = SimpleNamespace(pending_throne_input="cool")
    registration = SimpleNamespace(
        register_domme=AsyncMock(
            return_value=SimpleNamespace(
                domme=SimpleNamespace(id=1),
                webhook_url="https://example/webhook/abc",
            )
        )
    )
    service = _service(onboarding=onboarding, registration=registration)

    webhook = asyncio.run(
        service.confirm_identity(guild_id=TEST_GUILD_ID, discord_user_id=42)
    )

    registration.register_domme.assert_awaited_once_with(
        guild_id=TEST_GUILD_ID, discord_user_id=42, throne_input="cool"
    )
    assert webhook == "https://example/webhook/abc"
    assert onboarding.stage_calls[-1] == (TEST_GUILD_ID, 42, STAGE_AWAITING_WEBHOOK_TEST)


def test_confirm_identity_without_pending_input_raises():
    onboarding = _FakeOnboardingRepo()
    onboarding.state = None
    service = _service(onboarding=onboarding)
    with pytest.raises(OnboardingError):
        asyncio.run(service.confirm_identity(guild_id=TEST_GUILD_ID, discord_user_id=42))


def test_reject_identity_returns_to_throne_input_stage():
    onboarding = _FakeOnboardingRepo()
    service = _service(onboarding=onboarding)
    asyncio.run(service.reject_identity(guild_id=TEST_GUILD_ID, discord_user_id=42))
    assert onboarding.stage_calls == [(TEST_GUILD_ID, 42, STAGE_AWAITING_THRONE_INPUT)]


def test_mark_webhook_received_moves_to_preferences():
    onboarding = _FakeOnboardingRepo()
    service = _service(onboarding=onboarding)
    asyncio.run(service.mark_webhook_received(guild_id=TEST_GUILD_ID, discord_user_id=42))
    assert onboarding.stage_calls == [(TEST_GUILD_ID, 42, STAGE_AWAITING_PREFERENCES)]


def test_save_preferences_persists_and_completes():
    onboarding = _FakeOnboardingRepo()
    dommes = _FakeDommesRepo(SimpleNamespace(id=1))
    service = _service(onboarding=onboarding, dommes=dommes)

    asyncio.run(
        service.save_preferences(
            guild_id=TEST_GUILD_ID,
            discord_user_id=42,
        )
    )

    assert dommes.set_preferences_calls == [
        {
            "guild_id": TEST_GUILD_ID,
            "discord_user_id": 42,
            "confirm": True,
        }
    ]
    assert onboarding.complete_calls == [(TEST_GUILD_ID, 42)]
    # the service explicitly sets stage to STAGE_COMPLETE only via complete()
    _ = STAGE_COMPLETE  # imported for clarity


def test_save_preferences_requires_registered_domme():
    dommes = _FakeDommesRepo(None)
    service = _service(dommes=dommes)
    with pytest.raises(OnboardingError):
        asyncio.run(
            service.save_preferences(
                guild_id=TEST_GUILD_ID,
                discord_user_id=42,
            )
        )


def test_defer_migration_sets_future_timestamp():
    dommes = _FakeDommesRepo(SimpleNamespace(id=1))
    service = _service(dommes=dommes)
    asyncio.run(
        service.defer_migration(guild_id=TEST_GUILD_ID, discord_user_id=42, days=7)
    )
    assert len(dommes.defer_preferences_calls) == 1
    call = dommes.defer_preferences_calls[0]
    assert call["guild_id"] == TEST_GUILD_ID
    assert call["discord_user_id"] == 42
    assert call["until"] > datetime.now(timezone.utc)


def test_defer_migration_outside_new_system_guild_raises():
    service = _service()
    with pytest.raises(OnboardingError):
        asyncio.run(
            service.defer_migration(guild_id=OTHER_GUILD_ID, discord_user_id=42)
        )
