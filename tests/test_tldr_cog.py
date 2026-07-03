from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import discord
from discord import app_commands

from rob.discord.cogs.tldr import TldrCog
from rob.services.tldr_service import TldrResult


def _settings(**overrides):
    base = dict(
        tldr_enabled=True,
        tldr_cooldown_seconds=30,
        tldr_max_messages=400,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class _Response:
    def __init__(self):
        self.messages: list[dict] = []
        self.deferred = False

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)

    async def defer(self, **kwargs):
        self.deferred = True


class _Followup:
    def __init__(self):
        self.messages: list[dict] = []

    async def send(self, **kwargs):
        self.messages.append(kwargs)


def _perms(view=True, history=True):
    return SimpleNamespace(view_channel=view, read_message_history=history)


def _channel(name="general", user_perms=None, bot_perms=None):
    channel = MagicMock(spec=discord.TextChannel)
    channel.name = name
    bot_member = MagicMock(spec=discord.Member)
    channel.guild = SimpleNamespace(me=bot_member)
    up = user_perms or _perms()
    bp = bot_perms or _perms()

    def permissions_for(who):
        return bp if who is bot_member else up

    channel.permissions_for = permissions_for
    return channel


def _interaction(*, channel, guild_id=1, user_id=10):
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    deleted = []

    async def _delete_original_response():
        deleted.append(True)

    return SimpleNamespace(
        user=user,
        guild=SimpleNamespace(id=guild_id, get_member=lambda _id: user),
        channel=channel,
        response=_Response(),
        followup=_Followup(),
        delete_original_response=_delete_original_response,
        deleted_original=deleted,
    )


def _bot(summary_result=None, settings=None):
    async def _summarize(messages, *, topic, timeframe_label, channel_name):
        _bot.last_call = {
            "messages": messages,
            "topic": topic,
            "timeframe_label": timeframe_label,
            "channel_name": channel_name,
        }
        return summary_result or TldrResult(
            summary="a summary",
            method="digest",
            message_count=len(messages),
            participant_count=1,
            topic=topic,
        )

    return SimpleNamespace(
        settings=settings or _settings(),
        tldr_service=SimpleNamespace(summarize=_summarize),
    )


def _cog(bot, messages=None):
    cog = TldrCog(bot)

    async def _collect(channel, after):
        return messages if messages is not None else []

    cog._collect_messages = _collect  # type: ignore[method-assign]
    return cog


def test_disabled_feature_replies_with_error():
    bot = _bot(settings=_settings(tldr_enabled=False))
    cog = _cog(bot)
    interaction = _interaction(channel=_channel())
    asyncio.run(TldrCog.tldr.callback(cog, interaction))
    assert interaction.response.messages
    assert interaction.response.messages[0]["ephemeral"] is True
    assert not interaction.response.deferred


def test_cooldown_blocks_second_call():
    from rob.services.tldr_service import ChatMessage
    from datetime import datetime, timezone

    msgs = [ChatMessage("A", "hello world this is a message", datetime.now(timezone.utc))]
    bot = _bot()
    cog = _cog(bot, messages=msgs)

    first = _interaction(channel=_channel())
    asyncio.run(TldrCog.tldr.callback(cog, first))
    assert first.response.deferred
    assert first.followup.messages  # success on first call

    second = _interaction(channel=_channel())
    asyncio.run(TldrCog.tldr.callback(cog, second))
    # On cooldown: ephemeral short-circuit, no defer, no summariser followup.
    assert not second.response.deferred
    assert second.response.messages
    assert second.response.messages[0]["ephemeral"] is True
    assert not second.followup.messages


def test_user_without_read_access_is_refused():
    bot = _bot()
    cog = _cog(bot)
    interaction = _interaction(channel=_channel(user_perms=_perms(view=False)))
    asyncio.run(TldrCog.tldr.callback(cog, interaction))
    assert interaction.response.messages
    assert not interaction.response.deferred


def test_bot_without_read_access_is_refused():
    bot = _bot()
    cog = _cog(bot)
    interaction = _interaction(channel=_channel(bot_perms=_perms(history=False)))
    asyncio.run(TldrCog.tldr.callback(cog, interaction))
    assert interaction.response.messages
    assert not interaction.response.deferred


def test_success_defaults_to_24h_and_sends_public_plain_message():
    from rob.services.tldr_service import ChatMessage
    from datetime import datetime, timezone

    msgs = [ChatMessage("A", "hello world this is a message", datetime.now(timezone.utc))]
    bot = _bot()
    cog = _cog(bot, messages=msgs)
    interaction = _interaction(channel=_channel())

    asyncio.run(TldrCog.tldr.callback(cog, interaction))

    assert interaction.response.deferred
    assert interaction.followup.messages
    sent = interaction.followup.messages[0]
    # Public, plain-text reply: no ephemeral flag, no card/view/embed.
    assert sent.get("ephemeral") is not True
    assert "view" not in sent and "embeds" not in sent
    assert "a summary" in sent["content"]
    assert isinstance(sent["allowed_mentions"], discord.AllowedMentions)
    assert _bot.last_call["timeframe_label"] == "the last 24 hours"


def test_summarize_failure_after_defer_removes_public_thinking_and_errors_privately():
    from rob.services.tldr_service import ChatMessage
    from datetime import datetime, timezone

    msgs = [ChatMessage("A", "hello world this is a message", datetime.now(timezone.utc))]

    async def _boom(messages, *, topic, timeframe_label, channel_name):
        raise RuntimeError("summariser exploded")

    bot = SimpleNamespace(settings=_settings(), tldr_service=SimpleNamespace(summarize=_boom))
    cog = _cog(bot, messages=msgs)
    interaction = _interaction(channel=_channel())

    asyncio.run(TldrCog.tldr.callback(cog, interaction))

    assert interaction.response.deferred
    assert interaction.deleted_original  # public "thinking…" removed
    assert interaction.followup.messages
    assert interaction.followup.messages[0]["ephemeral"] is True


def test_topic_and_timeframe_choice_passed_through():
    from rob.services.tldr_service import ChatMessage
    from datetime import datetime, timezone

    msgs = [ChatMessage("A", "hello world this is a message", datetime.now(timezone.utc))]
    bot = _bot()
    cog = _cog(bot, messages=msgs)
    interaction = _interaction(channel=_channel())

    choice = app_commands.Choice(name="Last 6 hours", value="6h")
    asyncio.run(TldrCog.tldr.callback(cog, interaction, timeframe=choice, topic="  events "))

    assert _bot.last_call["topic"] == "events"
    assert _bot.last_call["timeframe_label"] == "the last 6 hours"
