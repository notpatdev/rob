from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import discord

from rob.database.repositories.public_summary import (
    CurrencyTotal,
    GuildSummary,
    TopReceiver,
)
from rob.services import final_sequence as fs
from rob.services.final_sequence import (
    ANONYMISED_KEY,
    COMPLETED_KEY,
    DMS_SENT_KEY,
    STATS_POSTED_KEY,
    FinalSequenceService,
)
from rob.services.maintenance_service import MaintenanceService
from tests.services.test_wind_down import _FakeBotState


def _forbidden() -> discord.HTTPException:
    return discord.HTTPException(
        SimpleNamespace(status=403, reason="Forbidden"), "cannot send"
    )


class _FakeUser:
    def __init__(self, user_id: int, events: list[str], *, raise_on_send=False):
        self.id = user_id
        self.display_name = f"user-{user_id}"
        self.messages: list[dict] = []
        self._events = events
        self.raise_on_send = raise_on_send

    async def send(self, **kwargs):
        if self.raise_on_send:
            raise _forbidden()
        self._events.append("dm")
        self.messages.append(kwargs)


class _FakeSendsRepo:
    def __init__(self, events: list[str], *, rows_by_user=None):
        self._events = events
        self._rows_by_user = rows_by_user or {}
        self.recipient_calls: list[tuple[int, int]] = []
        self.anonymise_calls: list[int] = []

    async def counted_sends_for_recipient(self, guild_id, user_id):
        self.recipient_calls.append((guild_id, user_id))
        return list(self._rows_by_user.get(user_id, []))

    async def anonymise_guild_sends(self, guild_id):
        self._events.append("anonymise")
        self.anonymise_calls.append(guild_id)
        return 5


class _FakeBot:
    def __init__(
        self,
        *,
        state,
        maintenance,
        sends_repo,
        dommes=(),
        subs=(),
        blacklisted=(),
        users=None,
    ):
        self.bot_settings_repo = state
        self.maintenance_service = maintenance
        self.sends_repo = sends_repo
        self.database = object()
        self._dommes = list(dommes)
        self._subs = list(subs)
        self._blacklisted = set(blacklisted)
        self.users = users or {}
        self.dommes_repo = SimpleNamespace(list_for_guild=self._list_dommes)
        self.subs_repo = SimpleNamespace(list_for_guild=self._list_subs)
        self.blacklist_repo = SimpleNamespace(contains=self._contains)

    async def _list_dommes(self, guild_id):
        return list(self._dommes)

    async def _list_subs(self, guild_id):
        return list(self._subs)

    async def _contains(self, user_id):
        return user_id in self._blacklisted

    def get_user(self, user_id):
        return self.users.get(user_id)

    async def fetch_user(self, user_id):
        if user_id in self.users:
            return self.users[user_id]
        raise discord.HTTPException(SimpleNamespace(status=404, reason="x"), "x")


def _entry(user_id: int) -> SimpleNamespace:
    return SimpleNamespace(discord_user_id=user_id)


class _RecordingService(FinalSequenceService):
    """Final sequence with the stats step stubbed to a recorded marker (the real
    stats post needs a live Discord channel, exercised separately)."""

    def __init__(self, *args, events, **kwargs):
        super().__init__(*args, **kwargs)
        self._events = events

    async def _post_final_stats(self) -> None:
        self._events.append("stats")


def _build(*, phase, events, dommes=(), subs=(), blacklisted=(), users=None):
    state = _FakeBotState()
    maintenance = MaintenanceService(state)
    sends_repo = _FakeSendsRepo(events)
    bot = _FakeBot(
        state=state,
        maintenance=maintenance,
        sends_repo=sends_repo,
        dommes=dommes,
        subs=subs,
        blacklisted=blacklisted,
        users=users,
    )
    service = _RecordingService(bot=bot, events=events, send_delay_seconds=0)
    return service, bot, state, maintenance, sends_repo


def test_below_final_phase_does_nothing():
    async def run():
        events: list[str] = []
        service, bot, state, maintenance, sends_repo = _build(phase=0, events=events)
        await maintenance.set_wind_down_phase(2)

        assert await service.maybe_run() is False
        assert events == []
        assert sends_repo.anonymise_calls == []
        assert await state.get_bool(COMPLETED_KEY) is False

    asyncio.run(run())


def test_runs_all_three_steps_in_order():
    async def run():
        events: list[str] = []
        service, bot, state, maintenance, sends_repo = _build(
            phase=3,
            events=events,
            dommes=[_entry(1)],
            subs=[_entry(2)],
        )
        bot.users = {1: _FakeUser(1, events), 2: _FakeUser(2, events)}
        await maintenance.set_wind_down_phase(3)

        ran = await service.maybe_run()

        assert ran is True
        # Stats strictly first, anonymisation strictly last, DMs in between.
        assert events[0] == "stats"
        assert events[-1] == "anonymise"
        assert set(events[1:-1]) == {"dm"}
        assert events.count("anonymise") == 1
        # Each recipient's own history was queried for their PDF.
        assert sorted(sends_repo.recipient_calls) == [(service.guild_id, 1), (service.guild_id, 2)]
        assert sends_repo.anonymise_calls == [service.guild_id]
        # Every recipient got the farewell card and, separately, the keepsake
        # PDF (Components V2 views ship the file as its own message).
        for user in bot.users.values():
            assert len(user.messages) == 2
            assert any("view" in message for message in user.messages)
            assert any("file" in message for message in user.messages)
        # All step flags + completion are recorded.
        for key in (STATS_POSTED_KEY, DMS_SENT_KEY, ANONYMISED_KEY, COMPLETED_KEY):
            assert await state.get_bool(key) is True

    asyncio.run(run())


def test_second_run_is_a_noop():
    async def run():
        events: list[str] = []
        service, bot, state, maintenance, sends_repo = _build(
            phase=3, events=events, dommes=[_entry(1)]
        )
        bot.users = {1: _FakeUser(1, events)}
        await maintenance.set_wind_down_phase(3)

        assert await service.maybe_run() is True
        events.clear()
        # Already completed: a second call does nothing.
        assert await service.maybe_run() is False
        assert events == []
        assert sends_repo.anonymise_calls == [service.guild_id]  # not called again

    asyncio.run(run())


def test_resume_skips_completed_steps():
    async def run():
        events: list[str] = []
        service, bot, state, maintenance, sends_repo = _build(
            phase=3, events=events, dommes=[_entry(1)]
        )
        bot.users = {1: _FakeUser(1, events)}
        await maintenance.set_wind_down_phase(3)
        # Pretend stats + DMs already went out in an earlier (crashed) run.
        await state.set_bool(STATS_POSTED_KEY, True)
        await state.set_bool(DMS_SENT_KEY, True)

        assert await service.maybe_run() is True
        # Only anonymisation runs on resume — no re-post, no re-DM.
        assert events == ["anonymise"]
        assert bot.users[1].messages == []
        assert await state.get_bool(COMPLETED_KEY) is True

    asyncio.run(run())


def test_dm_failure_is_counted_and_anonymisation_still_runs():
    async def run():
        events: list[str] = []
        service, bot, state, maintenance, sends_repo = _build(
            phase=3, events=events, dommes=[_entry(1), _entry(2)]
        )
        bot.users = {
            1: _FakeUser(1, events, raise_on_send=True),  # DMs closed
            2: _FakeUser(2, events),
        }
        await maintenance.set_wind_down_phase(3)

        assert await service.maybe_run() is True
        # The bad recipient didn't stop the good one, and anonymisation still ran.
        assert bot.users[2].messages
        assert "anonymise" in events
        assert await state.get_bool(COMPLETED_KEY) is True

    asyncio.run(run())


def test_recipient_with_no_sends_still_gets_a_pdf():
    async def run():
        events: list[str] = []
        service, bot, state, maintenance, sends_repo = _build(
            phase=3, events=events, subs=[_entry(9)]
        )
        bot.users = {9: _FakeUser(9, events)}
        await maintenance.set_wind_down_phase(3)

        await service.maybe_run()

        # Card + keepsake file, even with an empty send history.
        assert len(bot.users[9].messages) == 2
        assert any("file" in message for message in bot.users[9].messages)

    asyncio.run(run())


# --- Final stats post (real _post_final_stats) ------------------------------


class _FakeChannel:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, **kwargs):
        self.sent.append(kwargs)


class _FakeSummaryRepo:
    def __init__(self, database):
        self.database = database

    async def guild_summary(self, *, guild_id):
        return GuildSummary(
            last_updated=datetime(2026, 8, 1, tzinfo=timezone.utc),
            total_count=2,
            domme_count=1,
            sub_count=1,
            totals=[CurrencyTotal("USD", 5000, 2)],
            top_receivers=[TopReceiver("Miss X", 5000, "USD", 2)],
        )


def test_post_final_stats_sends_summary_card_to_leaderboard(monkeypatch):
    async def run():
        state = _FakeBotState()
        maintenance = MaintenanceService(state)
        bot = _FakeBot(
            state=state, maintenance=maintenance, sends_repo=_FakeSendsRepo([])
        )
        service = FinalSequenceService(bot=bot, send_delay_seconds=0)

        monkeypatch.setattr(fs, "PublicSummaryRepository", _FakeSummaryRepo)
        channel = _FakeChannel()

        async def _channel():
            return channel

        service._leaderboard_channel = _channel  # type: ignore[method-assign]

        await service._post_final_stats()

        assert len(channel.sent) == 1
        assert "view" in channel.sent[0]

    asyncio.run(run())


def test_post_final_stats_without_channel_does_not_raise(monkeypatch):
    async def run():
        state = _FakeBotState()
        maintenance = MaintenanceService(state)
        bot = _FakeBot(
            state=state, maintenance=maintenance, sends_repo=_FakeSendsRepo([])
        )
        service = FinalSequenceService(bot=bot, send_delay_seconds=0)
        monkeypatch.setattr(fs, "PublicSummaryRepository", _FakeSummaryRepo)

        async def _no_channel():
            return None

        service._leaderboard_channel = _no_channel  # type: ignore[method-assign]

        # No channel -> logs and returns, never raises.
        await service._post_final_stats()

    asyncio.run(run())
