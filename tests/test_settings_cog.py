"""/preferences is offered on the main and test guilds and gated off elsewhere."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from rob.config.guilds import MAIN_GUILD_ID, TEST_GUILD_ID
from rob.discord.cogs.settings import SettingsCog
from rob.ui.cards.dm_onboarding import PreferencesView

OTHER_GUILD_ID = 424242424242424242


def _make_interaction(*, guild_id: int):
    response = MagicMock()
    response.send_message = AsyncMock()
    member = SimpleNamespace(id=7, roles=[])
    return SimpleNamespace(
        guild_id=guild_id,
        user=member,
        response=response,
    )


def _make_bot(*, access_role_id: int | None):
    bot = MagicMock()
    bot.guild_settings_repo = SimpleNamespace(
        get=AsyncMock(
            return_value=SimpleNamespace(leaderboard_view_role_id=access_role_id)
        )
    )
    return bot


def test_preferences_command_is_registered_for_main_and_test_guild():
    # Guards the scope widening from test-only to main + test.
    guild_ids = getattr(SettingsCog.preferences_command, "_guild_ids", None)
    assert guild_ids is not None
    assert MAIN_GUILD_ID in guild_ids
    assert TEST_GUILD_ID in guild_ids


def test_preferences_panel_offered_on_main_guild():
    bot = _make_bot(access_role_id=500)
    cog = SettingsCog(bot)
    interaction = _make_interaction(guild_id=MAIN_GUILD_ID)

    asyncio.run(cog._send_preferences_panel(interaction))

    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.await_args.kwargs
    assert isinstance(kwargs.get("view"), PreferencesView)
    assert kwargs.get("ephemeral") is True


def test_preferences_panel_unavailable_outside_new_system_guild():
    bot = _make_bot(access_role_id=500)
    cog = SettingsCog(bot)
    interaction = _make_interaction(guild_id=OTHER_GUILD_ID)

    asyncio.run(cog._send_preferences_panel(interaction))

    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.await_args.kwargs
    # Gating happens before the settings repo is consulted.
    assert not isinstance(kwargs.get("view"), PreferencesView)
    bot.guild_settings_repo.get.assert_not_awaited()
