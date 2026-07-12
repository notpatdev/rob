from __future__ import annotations

import asyncio
from types import SimpleNamespace

from rob.discord.cogs.counting import CountingCog
from rob.ui.emojis import ROBBLANK, ROBNO, ROBYES


class _FakeCountingService:
    def __init__(self, result):
        self.result = result

    async def process_message(self, _message):
        return self.result


class _FakeBot:
    def __init__(self, result):
        self.counting_service = _FakeCountingService(result)


class _FakeChannel:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace()


class _FakeMessage:
    def __init__(self):
        self.guild = SimpleNamespace(id=1)
        self.author = SimpleNamespace(bot=False)
        self.channel = _FakeChannel()
        self.deleted = False
        self.reactions: list[str] = []

    async def delete(self):
        self.deleted = True

    async def add_reaction(self, value: str):
        self.reactions.append(value)


def test_wrong_number_recovery_path_reacts_without_deleting():
    result = SimpleNamespace(
        success=False,
        reason="wrong_number_sub_recovery",
        blocked_until=None,
        reactions=(ROBBLANK, ROBNO),
    )
    cog = CountingCog(_FakeBot(result))  # type: ignore[arg-type]
    message = _FakeMessage()

    asyncio.run(cog.on_message(message))

    assert message.deleted is False
    assert message.reactions == [ROBBLANK, ROBNO]


def test_success_path_applies_all_requested_reactions():
    result = SimpleNamespace(
        success=True,
        reason=None,
        blocked_until=None,
        reactions=(ROBYES,),
    )
    cog = CountingCog(_FakeBot(result))  # type: ignore[arg-type]
    message = _FakeMessage()

    asyncio.run(cog.on_message(message))

    assert message.deleted is False
    assert message.reactions == [ROBYES]
