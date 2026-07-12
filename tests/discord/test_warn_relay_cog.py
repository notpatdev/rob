from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord

from rob.discord.cogs.warn_relay import WarnRelayCog


class _FakeUser:
    def __init__(self, user_id: int):
        self.id = user_id
        self.messages: list[dict[str, object]] = []

    async def send(self, content: str | None = None, **kwargs):
        payload: dict[str, object] = {"content": content}
        payload.update(kwargs)
        self.messages.append(payload)


class _FakeBot:
    def __init__(self):
        self.guild_settings_repo = SimpleNamespace(get=self._get_settings)
        self._user = _FakeUser(123)

    async def _get_settings(self, _guild_id: int):
        return SimpleNamespace(warn_log_channel_id=777, carlbot_user_id=555)

    def get_user(self, user_id: int):
        if user_id == 123:
            return self._user
        return None

    async def fetch_user(self, user_id: int):
        if user_id == 123:
            return self._user
        raise discord.NotFound(response=None, message="not found")  # pragma: no cover


def _warn_message(message_id: int = 1):
    embed = discord.Embed(title="Warn | Case #12")
    embed.add_field(name="Offender", value="<@123>", inline=False)
    return SimpleNamespace(
        id=message_id,
        guild=SimpleNamespace(id=42),
        channel=SimpleNamespace(id=777),
        author=SimpleNamespace(id=555),
        embeds=[embed],
        jump_url="https://discord.com/channels/x/y/z",
    )


def test_warn_relay_sends_courtesy_dm_and_dedupes():
    bot = _FakeBot()
    cog = WarnRelayCog(bot)
    msg = _warn_message(9001)

    asyncio.run(cog._process_carlbot_warn_message(msg))
    asyncio.run(cog._process_carlbot_warn_message(msg))

    assert len(bot._user.messages) == 1
    assert bot._user.messages[0].get("view") is not None
