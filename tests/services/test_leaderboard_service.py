from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from rob.database.repositories.models import LeaderboardEntry, LeaderboardSummary
from rob.services.leaderboard_service import LeaderboardService


class _FakeMessage:
    def __init__(self, message_id: int):
        self.id = message_id
        self.edits: list[dict] = []

    async def edit(self, **kwargs):
        self.edits.append(kwargs)


class _FakePartialMessage:
    def __init__(self, channel: "_FakeChannel", message_id: int):
        self.channel = channel
        self.id = message_id

    async def edit(self, **kwargs):
        if self.id not in self.channel._messages:
            raise KeyError(self.id)
        await self.channel._messages[self.id].edit(**kwargs)


class _FakeChannel:
    def __init__(self, *, channel_id: int = 999):
        self.id = channel_id
        self._messages: dict[int, _FakeMessage] = {}
        self.sends: list[dict] = []

    def get_partial_message(self, message_id: int):
        return _FakePartialMessage(self, message_id)

    async def send(self, **kwargs):
        self.sends.append(kwargs)
        message = _FakeMessage(len(self.sends))
        self._messages[message.id] = message
        return message


class _FakeGuild:
    def __init__(self, channel):
        self._channel = channel

    def get_channel(self, channel_id):
        if isinstance(self._channel, dict):
            return self._channel.get(channel_id)
        return self._channel

    async def fetch_channel(self, channel_id):
        channel = self.get_channel(channel_id)
        if channel is None:
            raise RuntimeError(channel_id)
        return channel


class _FakeBot:
    def __init__(self, guild):
        self._guild = guild

    def get_guild(self, _):
        return self._guild


class _FakeSettingsRepo:
    def __init__(
        self,
        *,
        registration_channel_id: int | None = None,
        leaderboard_channel_id: int | None = 123,
        send_track_channel_id: int | None = 321,
    ) -> None:
        self.registration_channel_id = registration_channel_id
        self.leaderboard_channel_id = leaderboard_channel_id
        self.send_track_channel_id = send_track_channel_id

    async def get(self, _):
        return SimpleNamespace(
            registration_channel_id=self.registration_channel_id,
            leaderboard_channel_id=self.leaderboard_channel_id,
            send_track_channel_id=self.send_track_channel_id,
        )

    async def list_guild_ids(self):
        return [1]


class _FakeBotStateRepo:
    def __init__(self):
        self.values: dict[str, str] = {}

    async def get_text(self, key: str):
        return self.values.get(key)

    async def set_value(self, key: str, value: str):
        self.values[key] = value


class _FakeMaintenance:
    def __init__(self, enabled: bool = False):
        self.enabled = enabled

    async def is_enabled(self) -> bool:
        return self.enabled

    async def get_leaderboard_status(self):
        return "maintenance" if self.enabled else "live"


class _FakeLeaderboardsRepo:
    def __init__(self):
        self.refs = {}
        self.upserts = []
        self.top_domme_limits: list[int] = []
        self.current_leader = LeaderboardEntry(label='@Domme', user_id=1, total_cents=1000, send_count=2)

    async def get_summary(self, *_args, **_kwargs):
        return LeaderboardSummary(total_cents=1000, send_count=2, domme_count=1, sub_count=1, unclaimed_send_count=0, unclaimed_total_cents=0)

    async def get_top_dommes(self, *_args, **_kwargs):
        self.top_domme_limits.append(int(_kwargs.get("limit", 0)))
        return [LeaderboardEntry(label='@Domme', user_id=1, total_cents=1000, send_count=2)]

    async def get_current_leader(self, *_args, **_kwargs):
        return self.current_leader

    async def get_message(self, guild_id, message_key):
        return self.refs.get((guild_id, message_key))

    async def upsert_message(self, **kwargs):
        self.upserts.append(kwargs)
        self.refs[(kwargs['guild_id'], kwargs['message_key'])] = SimpleNamespace(
            message_id=kwargs['message_id'],
            channel_id=kwargs['channel_id'],
        )


def _service(
    channel: _FakeChannel,
    *,
    repo: _FakeLeaderboardsRepo | None = None,
    state: _FakeBotStateRepo | None = None,
    maintenance: _FakeMaintenance | None = None,
    leaderboard_limit: int = 10,
    settings_repo: _FakeSettingsRepo | None = None,
) -> LeaderboardService:
    repo = repo or _FakeLeaderboardsRepo()
    state = state or _FakeBotStateRepo()
    maintenance = maintenance or _FakeMaintenance()
    settings_repo = settings_repo or _FakeSettingsRepo()
    return LeaderboardService(
        bot=_FakeBot(_FakeGuild(channel)),
        guild_settings=settings_repo,
        leaderboards=repo,
        bot_state=state,
        maintenance=maintenance,
        leaderboard_limit=leaderboard_limit,
        include_test_sends=False,
        test_gifter_usernames=("marie_123",),
        owner_test_user_id=None,
    )


def test_refresh_posts_main_and_stats_messages_not_sub_leaderboard(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("rob.services.leaderboard_service.discord.TextChannel", _FakeChannel)
    channel = _FakeChannel()
    service = _service(channel)

    ok = asyncio.run(service.refresh_guild(1))
    assert ok is True
    assert len(channel.sends) == 2

    first_text = "\n".join(str(getattr(x, "content", "")) for x in channel.sends[0]["view"].children[0].children)
    second_text = "\n".join(str(getattr(x, "content", "")) for x in channel.sends[1]["view"].children[0].children)
    assert "Thy Send Leaderboard" in first_text
    assert "-# 🟢 Live" in first_text
    assert "Thy Send Leaderboard | Stats" in second_text


def test_refresh_shows_maintenance_status_when_enabled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("rob.services.leaderboard_service.discord.TextChannel", _FakeChannel)
    channel = _FakeChannel()
    service = _service(channel, maintenance=_FakeMaintenance(enabled=True))

    asyncio.run(service.refresh_guild(1))

    first_text = "\n".join(str(getattr(x, "content", "")) for x in channel.sends[0]["view"].children[0].children)
    second_text = "\n".join(str(getattr(x, "content", "")) for x in channel.sends[1]["view"].children[0].children)
    assert "-# 🟠 Paused | Under Maintenance" in first_text
    assert "Rob is currently under maintenance" in second_text


def test_refresh_uses_new_message_keys_for_upsert(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("rob.services.leaderboard_service.discord.TextChannel", _FakeChannel)
    channel = _FakeChannel()
    repo = _FakeLeaderboardsRepo()
    service = _service(channel, repo=repo)

    asyncio.run(service.refresh_guild(1))

    keys = [u["message_key"] for u in repo.upserts]
    assert keys == ["leaderboard", "leaderboard_stats"]
    assert [u["leaderboard_type"] for u in repo.upserts] == ["discord", "discord"]


def test_refresh_edits_existing_messages_when_refs_exist(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("rob.services.leaderboard_service.discord.TextChannel", _FakeChannel)
    channel = _FakeChannel()
    existing_main = _FakeMessage(101)
    existing_stats = _FakeMessage(102)
    channel._messages[101] = existing_main
    channel._messages[102] = existing_stats
    repo = _FakeLeaderboardsRepo()
    repo.refs[(1, "leaderboard")] = SimpleNamespace(message_id=101, channel_id=channel.id)
    repo.refs[(1, "leaderboard_stats")] = SimpleNamespace(message_id=102, channel_id=channel.id)
    service = _service(channel, repo=repo)

    asyncio.run(service.refresh_guild(1))

    assert len(channel.sends) == 0
    assert len(existing_main.edits) == 1
    assert len(existing_stats.edits) == 1


def test_refresh_posts_new_when_referenced_message_missing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("rob.services.leaderboard_service.discord.TextChannel", _FakeChannel)
    channel = _FakeChannel()
    repo = _FakeLeaderboardsRepo()
    repo.refs[(1, "leaderboard")] = SimpleNamespace(message_id=101, channel_id=channel.id)
    repo.refs[(1, "leaderboard_stats")] = SimpleNamespace(message_id=102, channel_id=channel.id)
    service = _service(channel, repo=repo)

    asyncio.run(service.refresh_guild(1))

    assert len(channel.sends) == 2
    keys = [u["message_key"] for u in repo.upserts]
    assert keys == ["leaderboard", "leaderboard_stats"]


def test_refresh_recreates_missing_side_only_when_one_ref_is_missing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("rob.services.leaderboard_service.discord.TextChannel", _FakeChannel)
    channel = _FakeChannel()
    existing_main = _FakeMessage(101)
    channel._messages[101] = existing_main
    repo = _FakeLeaderboardsRepo()
    repo.refs[(1, "leaderboard")] = SimpleNamespace(message_id=101, channel_id=channel.id)
    service = _service(channel, repo=repo)

    asyncio.run(service.refresh_guild(1))

    assert len(existing_main.edits) == 1
    assert len(channel.sends) == 1
    keys = [u["message_key"] for u in repo.upserts]
    assert keys == ["leaderboard_stats"]


def test_refresh_recreates_full_pair_when_ref_channel_mismatches(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("rob.services.leaderboard_service.discord.TextChannel", _FakeChannel)
    channel = _FakeChannel()
    repo = _FakeLeaderboardsRepo()
    repo.refs[(1, "leaderboard")] = SimpleNamespace(message_id=101, channel_id=888)
    repo.refs[(1, "leaderboard_stats")] = SimpleNamespace(message_id=102, channel_id=888)
    service = _service(channel, repo=repo)

    asyncio.run(service.refresh_guild(1))

    assert len(channel.sends) == 2
    assert repo.refs[(1, "leaderboard")].channel_id == channel.id
    assert repo.refs[(1, "leaderboard_stats")].channel_id == channel.id


def test_refresh_skips_discord_edits_when_content_unchanged(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("rob.services.leaderboard_service.discord.TextChannel", _FakeChannel)
    channel = _FakeChannel()
    existing_main = _FakeMessage(101)
    existing_stats = _FakeMessage(102)
    channel._messages[101] = existing_main
    channel._messages[102] = existing_stats
    repo = _FakeLeaderboardsRepo()
    repo.refs[(1, "leaderboard")] = SimpleNamespace(message_id=101, channel_id=channel.id)
    repo.refs[(1, "leaderboard_stats")] = SimpleNamespace(message_id=102, channel_id=channel.id)
    service = _service(channel, repo=repo)

    asyncio.run(service.refresh_guild(1))
    asyncio.run(service.refresh_guild(1))

    assert len(existing_main.edits) == 1
    assert len(existing_stats.edits) == 1


def test_refresh_updates_discord_when_content_changes(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("rob.services.leaderboard_service.discord.TextChannel", _FakeChannel)
    channel = _FakeChannel()
    existing_main = _FakeMessage(101)
    existing_stats = _FakeMessage(102)
    channel._messages[101] = existing_main
    channel._messages[102] = existing_stats
    repo = _FakeLeaderboardsRepo()
    repo.refs[(1, "leaderboard")] = SimpleNamespace(message_id=101, channel_id=channel.id)
    repo.refs[(1, "leaderboard_stats")] = SimpleNamespace(message_id=102, channel_id=channel.id)
    service = _service(channel, repo=repo)

    asyncio.run(service.refresh_guild(1))
    repo.current_leader = LeaderboardEntry(label='@Domme', user_id=1, total_cents=9999, send_count=5)

    async def updated_get_top_dommes(*args, **kwargs):
        repo.top_domme_limits.append(int(kwargs.get("limit", 0)))
        return [LeaderboardEntry(label='@Domme', user_id=1, total_cents=9999, send_count=5)]

    repo.get_top_dommes = updated_get_top_dommes  # type: ignore[method-assign]

    async def updated_get_summary(*args, **kwargs):
        return LeaderboardSummary(total_cents=9999, send_count=5, domme_count=1, sub_count=1, unclaimed_send_count=0, unclaimed_total_cents=0)

    repo.get_summary = updated_get_summary  # type: ignore[method-assign]

    asyncio.run(service.refresh_guild(1))

    assert len(existing_main.edits) == 2
    assert len(existing_stats.edits) == 2


def test_refresh_skips_duplicate_concurrent_syncs(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("rob.services.leaderboard_service.discord.TextChannel", _FakeChannel)
    channel = _FakeChannel()
    service = _service(channel)

    original_ensure = service._ensure_message

    async def delayed_ensure(**kwargs):
        await asyncio.sleep(0.05)
        return await original_ensure(**kwargs)

    service._ensure_message = delayed_ensure  # type: ignore[method-assign]

    async def _run():
        first = asyncio.create_task(service.refresh_guild(1))
        await asyncio.sleep(0.01)
        second = asyncio.create_task(service.refresh_guild(1))
        return await asyncio.gather(first, second)

    results = asyncio.run(_run())
    assert results == [True, False]
    assert len(channel.sends) == 2


def test_refresh_uses_top_10_limit_for_public_leaderboard(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("rob.services.leaderboard_service.discord.TextChannel", _FakeChannel)
    channel = _FakeChannel()
    repo = _FakeLeaderboardsRepo()
    service = _service(channel, repo=repo, leaderboard_limit=25)

    asyncio.run(service.refresh_guild(1))

    assert repo.top_domme_limits == [10]
