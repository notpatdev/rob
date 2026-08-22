from __future__ import annotations

from types import SimpleNamespace

import discord
import pytest
from discord import app_commands

from bill.components.setup import (
    SetupChannelDynamic,
    _resolve_selected_text_channel,
    missing_channel_permissions,
    setup_custom_id,
)
from bill.worker_client import GuildSetupSession


class FakeTextChannel:
    def __init__(self, channel_id: int, guild_id: int) -> None:
        self.id = channel_id
        self.guild = SimpleNamespace(id=guild_id)
        self.type = discord.ChannelType.text
        self.mention = f"<#{channel_id}>"

    def permissions_for(self, _member: object) -> discord.Permissions:
        return discord.Permissions.all()


class FakeGuild:
    def __init__(self, guild_id: int, channel: object | None = None) -> None:
        self.id = guild_id
        self.me = SimpleNamespace(id=999)
        self.channel = channel
        self.requested_channel_ids: list[int] = []

    def get_channel(self, channel_id: int) -> object | None:
        self.requested_channel_ids.append(channel_id)
        return self.channel


class FakeClient:
    def __init__(
        self,
        fetched_channel: object | None = None,
        fetch_error: discord.HTTPException | None = None,
    ) -> None:
        self.fetched_channel = fetched_channel
        self.fetch_error = fetch_error
        self.fetched_channel_ids: list[int] = []

    async def fetch_channel(self, channel_id: int) -> object:
        self.fetched_channel_ids.append(channel_id)
        if self.fetch_error is not None:
            raise self.fetch_error
        assert self.fetched_channel is not None
        return self.fetched_channel


class FakeResponse:
    def __init__(self) -> None:
        self.messages: list[tuple[str, bool]] = []
        self.edited_view: discord.ui.LayoutView | None = None

    async def send_message(self, content: str, *, ephemeral: bool) -> None:
        self.messages.append((content, ephemeral))

    async def edit_message(self, *, view: discord.ui.LayoutView) -> None:
        self.edited_view = view


class FakeWorker:
    def __init__(self, session: GuildSetupSession) -> None:
        self.session = session
        self.saved_channel_id: int | None = None

    async def get_guild_setup(self, _session_id: str) -> GuildSetupSession:
        return self.session

    async def set_guild_setup_channel(
        self,
        _session_id: str,
        *,
        guild_id: int,
        initiator_user_id: int,
        expected_revision: int,
        channel_id: int,
    ) -> GuildSetupSession:
        assert (guild_id, initiator_user_id, expected_revision) == (20, 10, 3)
        self.saved_channel_id = channel_id
        return GuildSetupSession(
            "setup",
            "20",
            "10",
            "active",
            "confirm",
            str(channel_id),
            4,
            None,
            None,
            None,
            None,
            None,
        )


class FakeBot(FakeClient):
    def __init__(self, worker: FakeWorker, fetched_channel: object | None = None) -> None:
        super().__init__(fetched_channel)
        self.worker = worker

    def require_worker(self) -> FakeWorker:
        return self.worker


def partial_channel(
    channel_id: int = 30,
    *,
    guild_id: int = 20,
    channel_type: discord.ChannelType = discord.ChannelType.text,
) -> app_commands.AppCommandChannel:
    client = discord.Client(intents=discord.Intents.none())
    return app_commands.AppCommandChannel(
        state=client._connection,
        data={
            "id": channel_id,
            "type": channel_type.value,
            "name": "bill-sends",
            "permissions": str(discord.Permissions.all().value),
        },
        guild_id=guild_id,
    )


def partial_thread(channel_id: int = 30, *, guild_id: int = 20) -> app_commands.AppCommandThread:
    client = discord.Client(intents=discord.Intents.none())
    return app_commands.AppCommandThread(
        state=client._connection,
        data={
            "id": channel_id,
            "type": discord.ChannelType.public_thread.value,
            "name": "bill-thread",
            "permissions": str(discord.Permissions.all().value),
            "parent_id": "29",
            "owner_id": "10",
            "member_count": 1,
            "message_count": 1,
            "thread_metadata": {
                "archived": False,
                "auto_archive_duration": 60,
                "archive_timestamp": "2026-08-22T00:00:00+00:00",
            },
        },
        guild_id=guild_id,
    )


def test_missing_channel_permissions_lists_actionable_names() -> None:
    permissions = discord.Permissions.none()
    permissions.view_channel = True

    assert missing_channel_permissions(permissions) == (
        "Send Messages",
        "Embed Links",
        "Read Message History",
    )


def test_complete_channel_permissions_passes() -> None:
    permissions = discord.Permissions.none()
    permissions.update(
        view_channel=True,
        send_messages=True,
        embed_links=True,
        read_message_history=True,
    )

    assert missing_channel_permissions(permissions) == ()


@pytest.mark.asyncio
async def test_selected_partial_channel_resolves_from_guild_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel = FakeTextChannel(30, 20)
    guild = FakeGuild(20, channel)
    client = FakeClient()
    monkeypatch.setattr("bill.components.setup.discord.TextChannel", FakeTextChannel)

    resolved = await _resolve_selected_text_channel(
        partial_channel(), guild=guild, client=client  # type: ignore[arg-type]
    )

    assert resolved is channel
    assert guild.requested_channel_ids == [30]
    assert client.fetched_channel_ids == []


@pytest.mark.asyncio
async def test_selected_partial_channel_fetches_when_not_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel = FakeTextChannel(30, 20)
    guild = FakeGuild(20)
    client = FakeClient(channel)
    monkeypatch.setattr("bill.components.setup.discord.TextChannel", FakeTextChannel)

    resolved = await _resolve_selected_text_channel(
        partial_channel(), guild=guild, client=client  # type: ignore[arg-type]
    )

    assert resolved is channel
    assert client.fetched_channel_ids == [30]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "selected",
    [
        partial_channel(guild_id=21),
        partial_channel(channel_type=discord.ChannelType.voice),
        partial_channel(channel_type=discord.ChannelType.category),
        partial_channel(channel_type=discord.ChannelType.forum),
        partial_channel(channel_type=discord.ChannelType.stage_voice),
        partial_channel(channel_type=discord.ChannelType.private),
        partial_thread(),
    ],
)
async def test_selected_partial_channel_rejects_wrong_guild_and_types(
    selected: app_commands.AppCommandChannel | app_commands.AppCommandThread,
) -> None:
    guild = FakeGuild(20)
    client = FakeClient()

    assert (
        await _resolve_selected_text_channel(
            selected, guild=guild, client=client  # type: ignore[arg-type]
        )
        is None
    )
    assert guild.requested_channel_ids == []
    assert client.fetched_channel_ids == []


@pytest.mark.asyncio
async def test_selected_partial_channel_rejects_fetched_channel_from_wrong_guild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guild = FakeGuild(20)
    client = FakeClient(FakeTextChannel(30, 21))
    monkeypatch.setattr("bill.components.setup.discord.TextChannel", FakeTextChannel)

    assert (
        await _resolve_selected_text_channel(
            partial_channel(), guild=guild, client=client  # type: ignore[arg-type]
        )
        is None
    )


@pytest.mark.asyncio
async def test_channel_callback_surfaces_fetch_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = FakeResponse()
    session = GuildSetupSession(
        "setup", "20", "10", "active", "select_channel", None, 3, None, None, None, None, None
    )
    http_response = SimpleNamespace(status=503, reason="Unavailable", text="Unavailable")
    bot = FakeBot(FakeWorker(session))
    bot.fetch_error = discord.HTTPException(http_response, "temporary")  # type: ignore[arg-type]
    interaction = SimpleNamespace(
        client=bot,
        guild=FakeGuild(20),
        guild_id=20,
        user=SimpleNamespace(id=10),
        response=response,
    )
    select = discord.ui.ChannelSelect(custom_id=setup_custom_id(session, "channel"))
    select._values = [partial_channel()]
    dynamic = SetupChannelDynamic(select, "setup", "10", "20", 3)

    async def authorized(*_args: object) -> bool:
        return True

    monkeypatch.setattr("bill.components.setup._authorized_setup", authorized)
    await dynamic.callback(interaction)  # type: ignore[arg-type]

    assert response.messages == [
        ("Bill could not load that channel. Please try again.", True)
    ]


@pytest.mark.asyncio
async def test_channel_callback_saves_resolved_channel_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = FakeResponse()
    session = GuildSetupSession(
        "setup", "20", "10", "active", "select_channel", None, 3, None, None, None, None, None
    )
    worker = FakeWorker(session)
    channel = FakeTextChannel(30, 20)
    bot = FakeBot(worker, channel)
    interaction = SimpleNamespace(
        client=bot,
        guild=FakeGuild(20),
        guild_id=20,
        user=SimpleNamespace(id=10),
        response=response,
    )
    select = discord.ui.ChannelSelect(custom_id=setup_custom_id(session, "channel"))
    select._values = [partial_channel()]
    dynamic = SetupChannelDynamic(select, "setup", "10", "20", 3)

    async def authorized(*_args: object) -> bool:
        return True

    monkeypatch.setattr("bill.components.setup.discord.TextChannel", FakeTextChannel)
    monkeypatch.setattr("bill.components.setup._authorized_setup", authorized)
    await dynamic.callback(interaction)  # type: ignore[arg-type]

    assert worker.saved_channel_id == 30
    assert bot.fetched_channel_ids == [30]
    assert response.edited_view is not None
