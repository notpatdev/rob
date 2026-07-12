"""Privacy / right-to-erasure cog tests, using fakes (no Postgres, no
Discord network)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from rob.config.guilds import MAIN_GUILD_ID, TEST_GUILD_ID
from rob.discord.cogs.data_privacy import DataPrivacyCog, _ForgetMeView, _summarize


def _fake_repo(*, guilds=None, deleted=None):
    repo = SimpleNamespace()
    repo.guilds_with_user_data = AsyncMock(return_value=list(guilds or []))
    repo.delete_user_everywhere = AsyncMock(
        return_value=dict(deleted or {"subs": 1, "sends": 2})
    )
    repo.delete_user_in_guild = AsyncMock(
        return_value=dict(deleted or {"subs": 1})
    )
    return repo


def _fake_bot(repo):
    return SimpleNamespace(user_data_repo=repo)


def _fake_interaction(*, user_id=7, guild_id=None):
    response = MagicMock()
    response.send_message = AsyncMock()
    response.edit_message = AsyncMock()
    return SimpleNamespace(
        user=SimpleNamespace(id=user_id),
        guild_id=guild_id,
        response=response,
    )


def _fake_member(*, user_id=7, guild_id=MAIN_GUILD_ID):
    return SimpleNamespace(id=user_id, guild=SimpleNamespace(id=guild_id))


def test_member_remove_from_main_guild_wipes_everywhere():
    repo = _fake_repo()
    cog = DataPrivacyCog(_fake_bot(repo))

    asyncio.run(cog.on_member_remove(_fake_member(user_id=99, guild_id=MAIN_GUILD_ID)))

    repo.delete_user_everywhere.assert_awaited_once_with(99)
    repo.delete_user_in_guild.assert_not_awaited()


def test_member_remove_from_other_guild_does_nothing():
    repo = _fake_repo()
    cog = DataPrivacyCog(_fake_bot(repo))

    asyncio.run(cog.on_member_remove(_fake_member(user_id=99, guild_id=TEST_GUILD_ID)))
    asyncio.run(cog.on_member_remove(_fake_member(user_id=99, guild_id=123456789)))

    repo.delete_user_everywhere.assert_not_awaited()
    repo.delete_user_in_guild.assert_not_awaited()


def test_member_remove_swallows_repo_errors():
    repo = _fake_repo()
    repo.delete_user_everywhere = AsyncMock(side_effect=RuntimeError("db down"))
    cog = DataPrivacyCog(_fake_bot(repo))

    # Must not raise: a failed auto-wipe is logged, not crashing the gateway.
    asyncio.run(cog.on_member_remove(_fake_member(guild_id=MAIN_GUILD_ID)))


def test_forgetme_single_guild_shows_simple_confirm():
    repo = _fake_repo(guilds=[111])
    cog = DataPrivacyCog(_fake_bot(repo))
    interaction = _fake_interaction(guild_id=111)

    asyncio.run(DataPrivacyCog.forgetme_command.callback(cog, interaction))

    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.await_args.kwargs
    assert kwargs["ephemeral"] is True
    view = kwargs["view"]
    labels = _button_labels(view)
    assert "Yes, delete everything" in labels
    assert "Cancel" in labels
    assert "Everywhere (all servers)" not in labels


def test_forgetme_multi_guild_offers_scope_choice():
    repo = _fake_repo(guilds=[111, 222])
    cog = DataPrivacyCog(_fake_bot(repo))
    interaction = _fake_interaction(guild_id=111)

    asyncio.run(DataPrivacyCog.forgetme_command.callback(cog, interaction))

    view = interaction.response.send_message.await_args.kwargs["view"]
    labels = _button_labels(view)
    assert "Just this server" in labels
    assert "Everywhere (all servers)" in labels
    assert "Cancel" in labels


def _button_labels(view) -> list[str]:
    labels: list[str] = []
    for child in view.walk_children():
        label = getattr(child, "label", None)
        if label:
            labels.append(label)
    return labels


def _button_by_label(view, label: str):
    for child in view.walk_children():
        if getattr(child, "label", None) == label:
            return child
    raise AssertionError(f"button not found: {label}")


def test_single_guild_confirm_wipes_everywhere():
    # A single-guild requester has data in at most one place, so the confirm
    # wipes everywhere; this also clears their guild-less terms record.
    repo = _fake_repo(guilds=[111], deleted={"subs": 1, "dommes": 1})
    cog = DataPrivacyCog(_fake_bot(repo))
    view = _ForgetMeView(cog=cog, user_id=7, guild_id=111, multi_guild=False)
    interaction = _fake_interaction(user_id=7, guild_id=111)

    confirm = _button_by_label(view, "Yes, delete everything")
    asyncio.run(confirm.callback(interaction))

    repo.delete_user_everywhere.assert_awaited_once_with(7)
    repo.delete_user_in_guild.assert_not_awaited()
    interaction.response.edit_message.assert_awaited_once()


def test_multi_guild_this_server_calls_delete_in_guild():
    repo = _fake_repo(guilds=[111, 222])
    cog = DataPrivacyCog(_fake_bot(repo))
    view = _ForgetMeView(cog=cog, user_id=7, guild_id=111, multi_guild=True)
    interaction = _fake_interaction(user_id=7, guild_id=111)

    this_server = _button_by_label(view, "Just this server")
    asyncio.run(this_server.callback(interaction))

    repo.delete_user_in_guild.assert_awaited_once_with(7, 111)
    repo.delete_user_everywhere.assert_not_awaited()


def test_multi_guild_everywhere_calls_delete_everywhere():
    repo = _fake_repo(guilds=[111, 222])
    cog = DataPrivacyCog(_fake_bot(repo))
    view = _ForgetMeView(cog=cog, user_id=7, guild_id=111, multi_guild=True)
    interaction = _fake_interaction(user_id=7, guild_id=111)

    everywhere = _button_by_label(view, "Everywhere (all servers)")
    asyncio.run(everywhere.callback(interaction))

    repo.delete_user_everywhere.assert_awaited_once_with(7)
    repo.delete_user_in_guild.assert_not_awaited()


def test_single_guild_confirm_in_dm_falls_back_to_everywhere():
    # No guild_id (DM context) means there's only one place the data lives.
    repo = _fake_repo(guilds=[111])
    cog = DataPrivacyCog(_fake_bot(repo))
    view = _ForgetMeView(cog=cog, user_id=7, guild_id=None, multi_guild=False)
    interaction = _fake_interaction(user_id=7, guild_id=None)

    confirm = _button_by_label(view, "Yes, delete everything")
    asyncio.run(confirm.callback(interaction))

    repo.delete_user_everywhere.assert_awaited_once_with(7)


def test_cancel_deletes_nothing():
    repo = _fake_repo(guilds=[111, 222])
    cog = DataPrivacyCog(_fake_bot(repo))
    view = _ForgetMeView(cog=cog, user_id=7, guild_id=111, multi_guild=True)
    interaction = _fake_interaction(user_id=7, guild_id=111)

    cancel = _button_by_label(view, "Cancel")
    asyncio.run(cancel.callback(interaction))

    repo.delete_user_everywhere.assert_not_awaited()
    repo.delete_user_in_guild.assert_not_awaited()
    interaction.response.edit_message.assert_awaited_once()


def test_confirmation_card_is_locked_to_requester():
    repo = _fake_repo(guilds=[111, 222])
    cog = DataPrivacyCog(_fake_bot(repo))
    view = _ForgetMeView(cog=cog, user_id=7, guild_id=111, multi_guild=True)
    other = _fake_interaction(user_id=999)

    allowed = asyncio.run(view.interaction_check(other))
    assert allowed is False
    other.response.send_message.assert_awaited_once()


def test_summary_reports_total_and_breakdown():
    total, body = _summarize({"subs": 1, "sends": 4, "dommes": 0})
    assert total == 5
    assert "**sends**: 4" in body
    assert "dommes" not in body  # zero-count tables are omitted


def test_summary_handles_empty():
    total, body = _summarize({"subs": 0, "sends": 0})
    assert total == 0
    assert "nothing" in body.lower()
