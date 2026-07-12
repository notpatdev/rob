from __future__ import annotations

import asyncio
from types import SimpleNamespace

import discord

from rob.config.guilds import BOT_OWNER_USER_IDS
from rob.discord.cogs.reports import ReportsCog


class _FakeDestination:
    def __init__(self, user_id: int):
        self.id = user_id
        self.messages: list[dict] = []

    async def send(self, **kwargs):
        self.messages.append(kwargs)


class _FakeResponse:
    def __init__(self):
        self.messages: list[dict] = []
        self.modal = None

    async def send_modal(self, modal):
        self.modal = modal

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)


class _FakeInteraction:
    def __init__(self):
        self.guild = SimpleNamespace(id=1, name="GuildName")
        self.user = SimpleNamespace(id=10, mention="<@10>")
        self.response = _FakeResponse()


class _FakeBot:
    def __init__(self):
        self.guild_settings_repo = SimpleNamespace(get=self._get_settings)
        self.destinations = {
            user_id: _FakeDestination(user_id) for user_id in BOT_OWNER_USER_IDS
        }
        self.requested_user_ids: list[int] = []

    async def _get_settings(self, _guild_id: int):
        return SimpleNamespace(report_channel_id=123)

    def get_user(self, user_id: int):
        self.requested_user_ids.append(user_id)
        return self.destinations[user_id]

    async def fetch_user(self, user_id: int):
        self.requested_user_ids.append(user_id)
        return self.destinations[user_id]


def test_report_command_opens_modal():
    bot = _FakeBot()
    cog = ReportsCog(bot)
    interaction = _FakeInteraction()

    asyncio.run(ReportsCog.report.callback(cog, interaction))

    assert interaction.response.modal is not None
    assert len(interaction.response.modal.children) == 3
    assert any(type(child).__name__ == "Label" for child in interaction.response.modal.children)
    assert any(
        isinstance(getattr(child, "component", None), discord.ui.FileUpload)
        for child in interaction.response.modal.children
        if type(child).__name__ == "Label"
    )


def test_report_requires_yes_acknowledgement():
    bot = _FakeBot()
    cog = ReportsCog(bot)
    interaction = _FakeInteraction()

    async def _destinations(_interaction):
        return [bot.destinations[user_id] for user_id in BOT_OWNER_USER_IDS]

    cog._resolve_destinations = _destinations  # type: ignore[method-assign]
    asyncio.run(
        cog.submit_report(
            interaction,
            issue_text="something broke",
            acknowledgement="NO",
            attachment=None,
        )
    )

    assert interaction.response.messages[0]["ephemeral"] is True


def test_report_posts_to_configured_destination_and_confirms_user():
    bot = _FakeBot()
    cog = ReportsCog(bot)
    interaction = _FakeInteraction()

    async def _destinations(_interaction):
        return [bot.destinations[user_id] for user_id in BOT_OWNER_USER_IDS]

    cog._resolve_destinations = _destinations  # type: ignore[method-assign]
    asyncio.run(
        cog.submit_report(
            interaction,
            issue_text="send queue stalled",
            acknowledgement="YES",
            attachment=None,
        )
    )

    assert all(destination.messages for destination in bot.destinations.values())
    assert interaction.response.messages[0]["ephemeral"] is True


def test_report_destinations_are_the_configured_bot_owners():
    # Reports must be DM'd to the configured bot-owner user ids, never a server
    # report channel or the Discord application owner.
    bot = _FakeBot()
    cog = ReportsCog(bot)
    interaction = _FakeInteraction()

    destinations = asyncio.run(cog._resolve_destinations(interaction))

    assert [destination.id for destination in destinations] == list(BOT_OWNER_USER_IDS)
    assert bot.requested_user_ids == list(BOT_OWNER_USER_IDS)


def test_report_modal_upload_is_forwarded_when_present():
    bot = _FakeBot()
    cog = ReportsCog(bot)
    interaction = _FakeInteraction()

    async def _destinations(_interaction):
        return [bot.destinations[user_id] for user_id in BOT_OWNER_USER_IDS]

    cog._resolve_destinations = _destinations  # type: ignore[method-assign]

    class _FakeAttachment:
        url = "https://example.test/screenshot.png"

        async def to_file(self, **_kwargs):
            return object()

    asyncio.run(
        cog.submit_report(
            interaction,
            issue_text="counting issue",
            acknowledgement="YES",
            attachment=_FakeAttachment(),
        )
    )

    # Components V2 cards suppress attachment previews, so the file goes out as
    # a separate follow-up message.
    assert all(
        any("file" in message for message in destination.messages)
        for destination in bot.destinations.values()
    )


def test_report_modal_upload_falls_back_to_attachment_read():
    bot = _FakeBot()
    cog = ReportsCog(bot)
    interaction = _FakeInteraction()

    async def _destinations(_interaction):
        return [bot.destinations[user_id] for user_id in BOT_OWNER_USER_IDS]

    cog._resolve_destinations = _destinations  # type: ignore[method-assign]

    class _FakeAttachment:
        filename = "screenshot.png"
        description = "Counting bug screenshot"
        url = "https://example.test/screenshot.png"

        async def to_file(self, **_kwargs):
            raise TypeError("older attachment path")

        async def read(self, **_kwargs):
            return b"image-bytes"

    asyncio.run(
        cog.submit_report(
            interaction,
            issue_text="counting issue",
            acknowledgement="YES",
            attachment=_FakeAttachment(),
        )
    )

    for destination in bot.destinations.values():
        file_messages = [message for message in destination.messages if "file" in message]
        assert file_messages
        assert file_messages[0]["file"].filename == "screenshot.png"
