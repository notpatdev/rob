"""Smoke tests for the DM-onboarding cards."""

from __future__ import annotations

import discord
import pytest

from rob.ui.cards.dm_onboarding import (
    ID_PREFS_LEADERBOARD_ACCESS,
    LEADERBOARD_ACCESS_OFF_VALUE,
    LEADERBOARD_ACCESS_ON_VALUE,
    PreferencesView,
    build_intro_modal,
    identity_confirm_card,
    intro_card,
    migration_prompt_card,
    preferences_card,
    success_card,
    webhook_setup_card,
    webhook_waiting_card,
)
from rob.ui.cards.dm_onboarding import onboarding_error_card  # noqa: E402


def _has_button_with_id(view: discord.ui.LayoutView, custom_id: str) -> bool:
    for item in view.walk_children():
        if isinstance(item, discord.ui.Button) and item.custom_id == custom_id:
            return True
    return False


def _has_select_with_id(view: discord.ui.LayoutView, custom_id: str) -> bool:
    for item in view.walk_children():
        if isinstance(item, discord.ui.Select) and item.custom_id == custom_id:
            return True
    return False


def test_intro_card_has_modal_open_button():
    rendered = intro_card()
    assert _has_button_with_id(rendered.view, "rob:dm_onboarding:intro:open_modal")


def test_intro_modal_has_throne_field():
    modal = build_intro_modal()
    assert modal.custom_id == "rob:dm_onboarding:intro:modal"
    children = list(modal.children)
    assert any(getattr(c, "custom_id", None) == "rob:dm_onboarding:intro:modal:throne_input" for c in children)


def test_identity_confirm_card_has_yes_and_no_buttons():
    rendered = identity_confirm_card(throne_handle="cool", throne_display_name="Cool")
    assert _has_button_with_id(rendered.view, "rob:dm_onboarding:identity:yes")
    assert _has_button_with_id(rendered.view, "rob:dm_onboarding:identity:no")


def test_webhook_setup_card_includes_webhook_url():
    rendered = webhook_setup_card(webhook_url="https://example.com/webhook/abc")
    found = False
    for item in rendered.view.walk_children():
        if isinstance(item, discord.ui.TextDisplay) and "https://example.com/webhook/abc" in item.content:
            found = True
            break
    assert found


def test_webhook_waiting_card_renders():
    rendered = webhook_waiting_card()
    assert rendered.view is not None


def test_preferences_view_defaults_and_save_button():
    view = PreferencesView(default_leaderboard_access=True)
    access_defaults = [o for o in view.leaderboard_access_select.options if o.default]
    assert access_defaults and access_defaults[0].value == LEADERBOARD_ACCESS_ON_VALUE

    assert view.save_button.custom_id == "rob:dm_onboarding:prefs:save"
    assert _has_button_with_id(view, "rob:dm_onboarding:prefs:save")


def test_preferences_card_returns_view():
    rendered = preferences_card()
    assert isinstance(rendered.view, PreferencesView)


def test_preferences_view_has_leaderboard_access_select():
    view = PreferencesView(default_leaderboard_access=True)
    assert view.leaderboard_access_select.custom_id == ID_PREFS_LEADERBOARD_ACCESS
    assert _has_select_with_id(view, ID_PREFS_LEADERBOARD_ACCESS)
    access_defaults = [o for o in view.leaderboard_access_select.options if o.default]
    assert access_defaults and access_defaults[0].value == LEADERBOARD_ACCESS_ON_VALUE


def test_preferences_view_chosen_access_defaults_false_and_reflects_selection():
    view = PreferencesView()
    assert view.chosen_leaderboard_access is False
    view.leaderboard_access_select._values = [LEADERBOARD_ACCESS_ON_VALUE]  # type: ignore[attr-defined]
    assert view.chosen_leaderboard_access is True
    view.leaderboard_access_select._values = [LEADERBOARD_ACCESS_OFF_VALUE]  # type: ignore[attr-defined]
    assert view.chosen_leaderboard_access is False


def test_preferences_view_renders_only_the_access_select():
    view = PreferencesView(show_leaderboard_access=True)
    rendered_select_ids = {
        item.custom_id
        for item in view.walk_children()
        if isinstance(item, discord.ui.Select)
    }
    assert rendered_select_ids == {ID_PREFS_LEADERBOARD_ACCESS}
    assert _has_button_with_id(view, "rob:dm_onboarding:prefs:save")


def test_success_card_messages_reflect_choices():
    rendered_on = success_card(leaderboard_access=True)
    rendered_off = success_card(leaderboard_access=False)
    on_text = " ".join(
        i.content for i in rendered_on.view.walk_children() if isinstance(i, discord.ui.TextDisplay)
    )
    off_text = " ".join(
        i.content for i in rendered_off.view.walk_children() if isinstance(i, discord.ui.TextDisplay)
    )
    assert "Leaderboard access on" in on_text
    assert "Leaderboard access off" in off_text


def test_migration_card_has_save_and_defer_buttons_inside_container():
    # The legacy "Open preferences" custom_id stays registered as a persistent
    # view (see DMOnboardingCog) so stale DMs still work, but the new card
    # deliberately doesn't render it.
    rendered = migration_prompt_card()
    assert _has_button_with_id(rendered.view, "rob:dm_migration:save")
    assert _has_button_with_id(rendered.view, "rob:dm_migration:defer_7d")
    assert not _has_button_with_id(rendered.view, "rob:dm_migration:open_prefs")


import asyncio  # noqa: E402

from unittest.mock import AsyncMock  # noqa: E402

from rob.ui.cards.dm_onboarding import (  # noqa: E402
    IdentityNoButton,
    IdentityYesButton,
    MigrationDeferButton,
    MigrationSaveButton,
    OpenModalButton,
    SavePrefsButton,
    WebhookRetryButton,
)


def _all_text(view) -> str:
    return " ".join(
        i.content
        for i in view.walk_children()
        if isinstance(i, discord.ui.TextDisplay)
    )


def _container_button_ids(view):
    ids: list[str] = []
    for top in view.children:
        if isinstance(top, discord.ui.Container):
            for child in top.walk_children():
                if isinstance(child, discord.ui.Button):
                    ids.append(child.custom_id)
    return ids


@pytest.mark.parametrize(
    "rendered",
    [
        intro_card(),
        identity_confirm_card(throne_handle="a", throne_display_name="A"),
        webhook_setup_card(webhook_url="https://x/y"),
        preferences_card(),
        success_card(),
        migration_prompt_card(),
        onboarding_error_card("oops"),
    ],
)
def test_no_literal_divider_text(rendered):
    text = _all_text(rendered.view)
    # Regression: dividers were once rendered as literal em-dash text.
    assert "——" not in text
    assert "——————————————" not in text


@pytest.mark.parametrize(
    "rendered,expected_ids",
    [
        (intro_card(), ["rob:dm_onboarding:intro:open_modal"]),
        (
            identity_confirm_card(throne_handle="a", throne_display_name="A"),
            [
                "rob:dm_onboarding:identity:yes",
                "rob:dm_onboarding:identity:no",
            ],
        ),
        (
            webhook_setup_card(webhook_url="https://x/y"),
            ["rob:dm_onboarding:webhook:retry"],
        ),
        (
            preferences_card(),
            ["rob:dm_onboarding:prefs:save"],
        ),
        (
            migration_prompt_card(),
            ["rob:dm_migration:save", "rob:dm_migration:defer_7d"],
        ),
        (
            onboarding_error_card("oops"),
            ["rob:dm_onboarding:intro:open_modal"],
        ),
    ],
)
def test_action_buttons_live_inside_container(rendered, expected_ids):
    button_ids = _container_button_ids(rendered.view)
    for expected in expected_ids:
        assert expected in button_ids, (
            f"button {expected!r} must live inside the Container, found {button_ids!r}"
        )


# Buttons must delegate to the cog; otherwise discord.py's default no-op
# callback fires and Discord shows "This interaction failed".


def _fake_interaction():
    from types import SimpleNamespace

    response = SimpleNamespace(
        send_message=AsyncMock(),
        defer=AsyncMock(),
        send_modal=AsyncMock(),
        edit_message=AsyncMock(),
    )
    user = SimpleNamespace(id=1, display_name="t", name="t")
    return SimpleNamespace(
        user=user,
        guild_id=None,
        channel_id=10,
        message=None,
        response=response,
        followup=SimpleNamespace(send=AsyncMock()),
        view=None,
        data={"custom_id": "x"},
    )


@pytest.mark.parametrize(
    "button_cls,handler_name",
    [
        (OpenModalButton, "handle_open_modal"),
        (IdentityYesButton, "handle_identity_yes"),
        (IdentityNoButton, "handle_identity_no"),
        (WebhookRetryButton, "handle_webhook_retry"),
        (SavePrefsButton, "handle_save_preferences"),
        (MigrationSaveButton, "handle_migration_save"),
        (MigrationDeferButton, "handle_migration_defer"),
    ],
)
def test_button_callback_routes_to_cog(button_cls, handler_name):
    cog = type(
        "CogStub",
        (),
        {handler_name: AsyncMock()},
    )()
    btn = button_cls(cog)
    interaction = _fake_interaction()
    asyncio.run(btn.callback(interaction))
    handler = getattr(cog, handler_name)
    if getattr(btn, "_PASS_VIEW", False):
        # Save buttons pass the live view; message components only carry defaults.
        handler.assert_awaited_once_with(interaction, view=btn.view)
    else:
        handler.assert_awaited_once_with(interaction)


def test_button_callback_without_cog_responds_gracefully():
    btn = OpenModalButton(None)
    interaction = _fake_interaction()
    asyncio.run(btn.callback(interaction))
    # Must still respond, or Discord shows "This interaction failed".
    interaction.response.send_message.assert_awaited_once()


def test_preferences_view_selects_ack_interactions():
    # Selects must defer, or Discord shows "This interaction failed" mid-selection.
    view = preferences_card().view
    interaction = _fake_interaction()
    asyncio.run(view.leaderboard_access_select.callback(interaction))
    interaction.response.defer.assert_awaited_once()
