from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from rob.models import CountBlock, CountRecoveryWindow, CountingState
from rob.services.counting_service import CountingService
from rob.ui.emojis import ROBBLANK, ROBNO, ROBYES


@dataclass
class _Role:
    id: int
    name: str


class _FakeMember:
    def __init__(self, user_id: int, roles: list[_Role], *, display_name: str = "Member", name: str = "member"):
        self.id = user_id
        self.bot = False
        self.roles = roles
        self.display_name = display_name
        self.name = name


class _FakeMessageRecord:
    def __init__(self):
        self.edits: list[dict] = []

    async def edit(self, **kwargs):
        self.edits.append(kwargs)


class _FakeChannel:
    def __init__(self, channel_id: int):
        self.id = channel_id
        self.sent: list[dict] = []
        self.messages: list[_FakeMessageRecord] = []

    async def send(self, **kwargs):
        self.sent.append(kwargs)
        msg = _FakeMessageRecord()
        self.messages.append(msg)
        return msg


class _FakeGuild:
    def __init__(self, guild_id: int, channel: _FakeChannel, members: list[_FakeMember]):
        self.id = guild_id
        self._channel = channel
        self._members = {member.id: member for member in members}

    def get_channel(self, channel_id: int):
        if channel_id == self._channel.id:
            return self._channel
        return None

    async def fetch_channel(self, channel_id: int):
        if channel_id == self._channel.id:
            return self._channel
        raise RuntimeError("channel not found")

    def get_member(self, user_id: int):
        return self._members.get(user_id)


class _FakeBot:
    def __init__(self, guild: _FakeGuild):
        self._guild = guild

    def get_guild(self, guild_id: int):
        if guild_id == self._guild.id:
            return self._guild
        return None


@dataclass
class _FakeMessageEvent:
    guild: _FakeGuild
    author: _FakeMember
    content: str
    channel: _FakeChannel
    attachments: list = None
    stickers: list = None

    def __post_init__(self):
        if self.attachments is None:
            self.attachments = []
        if self.stickers is None:
            self.stickers = []
        self.deleted = False

    async def delete(self):
        self.deleted = True


class _FakeCountingRepo:
    def __init__(self):
        self.state = CountingState(
            guild_id=1,
            channel_id=100,
            current_number=0,
            last_user_id=None,
            is_enabled=True,
            pending_restore=False,
            updated_at=datetime.now(timezone.utc),
        )
        self.next_window_id = 1
        self.next_block_id = 1
        self.windows: dict[int, CountRecoveryWindow] = {}
        self.blocks: dict[tuple[int, int], CountBlock] = {}

    async def get(self, guild_id: int):
        if guild_id != self.state.guild_id:
            return None
        return self.state

    async def upsert(self, **kwargs):
        self.state = CountingState(
            guild_id=kwargs["guild_id"],
            channel_id=kwargs["channel_id"],
            current_number=kwargs["current_number"],
            last_user_id=kwargs["last_user_id"],
            is_enabled=kwargs["is_enabled"],
            pending_restore=kwargs["pending_restore"],
            updated_at=datetime.now(timezone.utc),
        )
        return self.state

    async def create_recovery_window(self, **kwargs):
        window = CountRecoveryWindow(
            id=self.next_window_id,
            guild_id=kwargs["guild_id"],
            channel_id=kwargs["channel_id"],
            failed_user_id=kwargs["failed_user_id"],
            failed_user_role=kwargs["failed_user_role"],
            required_domme_user_id=kwargs["required_domme_user_id"],
            required_domme_id=kwargs["required_domme_id"],
            expected_number=kwargs["expected_number"],
            attempted_content=kwargs["attempted_content"],
            started_at=kwargs["started_at"],
            expires_at=kwargs["expires_at"],
            resolved_at=None,
            resolution=None,
            created_at=datetime.now(timezone.utc),
        )
        self.windows[self.next_window_id] = window
        self.next_window_id += 1
        return window

    async def get_active_recovery_window(self, guild_id: int, channel_id: int):
        active = [
            window
            for window in self.windows.values()
            if window.guild_id == guild_id
            and window.channel_id == channel_id
            and window.resolved_at is None
        ]
        if not active:
            return None
        return sorted(active, key=lambda value: value.id, reverse=True)[0]

    async def list_active_recovery_windows(self):
        return [
            window
            for window in self.windows.values()
            if window.resolved_at is None
        ]

    async def resolve_recovery_window(self, *, window_id: int, resolution: str):
        window = self.windows.get(window_id)
        if window is None or window.resolved_at is not None:
            return False
        self.windows[window_id] = CountRecoveryWindow(
            id=window.id,
            guild_id=window.guild_id,
            channel_id=window.channel_id,
            failed_user_id=window.failed_user_id,
            failed_user_role=window.failed_user_role,
            required_domme_user_id=window.required_domme_user_id,
            required_domme_id=window.required_domme_id,
            expected_number=window.expected_number,
            attempted_content=window.attempted_content,
            started_at=window.started_at,
            expires_at=window.expires_at,
            resolved_at=datetime.now(timezone.utc),
            resolution=resolution,
            created_at=window.created_at,
        )
        return True

    async def get_active_block(self, guild_id: int, discord_user_id: int, *, now: datetime | None = None):
        block = self.blocks.get((guild_id, discord_user_id))
        if block is None:
            return None
        cutoff = now or datetime.now(timezone.utc)
        if block.blocked_until <= cutoff:
            return None
        return block

    async def upsert_block(self, *, guild_id: int, discord_user_id: int, reason: str, blocked_until: datetime):
        block = CountBlock(
            id=self.next_block_id,
            guild_id=guild_id,
            discord_user_id=discord_user_id,
            reason=reason,
            blocked_until=blocked_until,
            created_at=datetime.now(timezone.utc),
        )
        self.next_block_id += 1
        self.blocks[(guild_id, discord_user_id)] = block
        return block


class _FakeGuildSettingsRepo:
    async def get(self, _guild_id: int):
        return SimpleNamespace(
            counting_channel_id=100,
            sub_role_id=22,
            domme_role_id=33,
        )


class _FakeDommesRepo:
    async def get_by_user_id(self, _guild_id: int, discord_user_id: int):
        if discord_user_id in {20, 21, 30}:
            return SimpleNamespace(id=discord_user_id + 1000)
        return None

    async def list_for_guild(self, _guild_id: int):
        return [
            SimpleNamespace(
                id=1020,
                discord_user_id=20,
                public_display_name="Miss Adore",
                throne_handle="missadore",
            ),
            SimpleNamespace(
                id=1021,
                discord_user_id=21,
                public_display_name="Lady Star",
                throne_handle="ladystar",
            ),
        ]


class _FakeBotSettings:
    def __init__(self):
        self.values: dict[str, str] = {}

    async def get_text(self, key: str):
        if key == "count_claimed_role_prefix":
            return "Claimed by "
        return self.values.get(key)

    async def set_value(self, key: str, value: str):
        self.values[key] = value


class _FakeSubsRepo:
    def __init__(self, send_names_by_user: dict[int, list[str]] | None = None) -> None:
        self._send_names_by_user = send_names_by_user or {}

    async def list_send_names_for_user(self, _guild_id: int, discord_user_id: int):
        names = self._send_names_by_user.get(discord_user_id, [])
        return [SimpleNamespace(send_name=name) for name in names]


def _service(*, repo: _FakeCountingRepo, guild: _FakeGuild, subs=None):
    bot_settings = _FakeBotSettings()
    return CountingService(
        bot=_FakeBot(guild),
        counting=repo,
        guild_settings=_FakeGuildSettingsRepo(),
        dommes=_FakeDommesRepo(),
        bot_settings=bot_settings,
        subs=subs,
        rescue_tick_seconds=1,
        rescue_window_seconds=300,
        block_seconds=12 * 60 * 60,
        parse_test_sends_as_real_sends=False,
        test_gifter_usernames=("marie_123",),
    )


@pytest.fixture(autouse=True)
def _patch_discord_member(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("rob.services.counting_service.discord.Member", _FakeMember)


def _make_setup():
    channel = _FakeChannel(channel_id=100)
    domme = _FakeMember(20, [_Role(33, "Dom/me")], display_name="Miss Adore", name="missadore")
    domme_alt = _FakeMember(21, [_Role(33, "Dom/me")], display_name="Lady Star", name="ladystar")
    sub = _FakeMember(10, [_Role(22, "Sub")], display_name="Subby", name="subby")
    claimed_sub = _FakeMember(
        11,
        [_Role(22, "Sub"), _Role(44, "Claimed by Miss Adore")],
        display_name="Claimed Sub",
        name="claimedsub",
    )
    guild = _FakeGuild(1, channel, [domme, domme_alt, sub, claimed_sub])
    repo = _FakeCountingRepo()
    service = _service(repo=repo, guild=guild)
    return service, repo, channel, guild, domme, domme_alt, sub, claimed_sub


def test_basic_math_expression_parser_accepts_valid_cases():
    assert CountingService.evaluate_expression("2 + 2") == 4
    assert CountingService.evaluate_expression("(2 + 2) * 3") == 12
    assert CountingService.evaluate_expression("10 / 2") == 5


@pytest.mark.parametrize(
    "expression",
    [
        "5 / 2",
        "__import__('os')",
        "open('/etc/passwd')",
        "1 / 0",
        "9" * 81,
    ],
)
def test_basic_math_expression_parser_rejects_unsafe_or_invalid_cases(expression: str):
    with pytest.raises(ValueError):
        CountingService.evaluate_expression(expression)


def test_non_numeric_and_invalid_math_messages_are_ignored():
    service, repo, _channel, guild, _domme, _domme_alt, sub, _claimed_sub = _make_setup()
    repo.state = CountingState(1, 100, 3, 9, True, False, datetime.now(timezone.utc))

    ignored_plain = asyncio.run(
        service.process_message(
            _FakeMessageEvent(guild=guild, author=sub, content="hello there", channel=guild.get_channel(100))
        )
    )
    ignored_invalid_math = asyncio.run(
        service.process_message(
            _FakeMessageEvent(guild=guild, author=sub, content="5 / 2", channel=guild.get_channel(100))
        )
    )

    assert ignored_plain is None
    assert ignored_invalid_math is None
    assert repo.state.current_number == 3


def test_existing_stale_count_state_auto_syncs_to_configured_channel():
    service, repo, _channel, guild, _domme, _domme_alt, sub, _claimed_sub = _make_setup()
    repo.state = CountingState(1, None, 0, None, False, False, datetime.now(timezone.utc))

    result = asyncio.run(
        service.process_message(
            _FakeMessageEvent(guild=guild, author=sub, content="1", channel=guild.get_channel(100))
        )
    )

    assert result is not None
    assert result.success is True
    assert repo.state.channel_id == 100
    assert repo.state.is_enabled is True
    assert repo.state.current_number == 1


def test_successful_count_reacts_with_robyes_and_updates_high_watermark():
    service, repo, _channel, guild, _domme, _domme_alt, sub, _claimed_sub = _make_setup()
    repo.state = CountingState(1, 100, 66, 9, True, False, datetime.now(timezone.utc))
    service.bot_settings.values["count_high_watermark:1"] = "66"

    result = asyncio.run(
        service.process_message(
            _FakeMessageEvent(guild=guild, author=sub, content="67", channel=guild.get_channel(100))
        )
    )

    assert result is not None
    assert result.success is True
    assert result.reactions == (ROBYES,)
    assert service.bot_settings.values["count_high_watermark:1"] == "67"


def test_domme_wrong_count_creates_recovery_window_and_qualifying_send_recovers():
    service, repo, _channel, _guild, domme, _domme_alt, _sub, _claimed_sub = _make_setup()
    repo.state = CountingState(1, 100, 7, 9, True, False, datetime.now(timezone.utc))
    message = _FakeMessageEvent(guild=service.bot.get_guild(1), author=domme, content="99", channel=service.bot.get_guild(1).get_channel(100))

    result = asyncio.run(service.process_message(message))

    assert result is not None
    assert result.reason == "wrong_number_domme_recovery"
    assert result.reactions == (ROBBLANK, ROBNO)
    assert repo.state.pending_restore is True
    active_windows = asyncio.run(repo.list_active_recovery_windows())
    assert len(active_windows) == 1
    assert active_windows[0].failed_user_role == "domme"

    recovered = asyncio.run(
        service.process_send_for_count_rescue(
            SimpleNamespace(
                guild_id=1,
                domme_user_id=domme.id,
                sub_user_id=10,
                sub_name="subby",
                sent_at=datetime.now(timezone.utc),
                is_private=False,
                is_test_send=False,
            )
        )
    )
    assert recovered is True
    assert message.deleted is True
    assert repo.state.pending_restore is False
    assert repo.state.current_number == 7


def test_domme_recovery_qualifying_send_with_unregistered_sub_recovers():
    service, repo, _channel, _guild, domme, _domme_alt, _sub, _claimed_sub = _make_setup()
    repo.state = CountingState(1, 100, 7, 9, True, False, datetime.now(timezone.utc))
    message = _FakeMessageEvent(guild=service.bot.get_guild(1), author=domme, content="99", channel=service.bot.get_guild(1).get_channel(100))

    asyncio.run(service.process_message(message))

    active_windows = asyncio.run(repo.list_active_recovery_windows())
    assert len(active_windows) == 1

    recovered = asyncio.run(
        service.process_send_for_count_rescue(
            SimpleNamespace(
                guild_id=1,
                domme_user_id=domme.id,
                sub_user_id=None,
                sub_name="unregistered_gifter",
                sent_at=datetime.now(timezone.utc),
                is_private=False,
                is_test_send=False,
            )
        )
    )
    assert recovered is True
    assert repo.state.pending_restore is False
    assert repo.state.current_number == 7


def test_domme_recovery_expiry_resets_count_to_one_visible_start():
    service, repo, _channel, _guild, domme, _domme_alt, _sub, _claimed_sub = _make_setup()
    repo.state = CountingState(1, 100, 7, 9, True, False, datetime.now(timezone.utc))
    message = _FakeMessageEvent(guild=service.bot.get_guild(1), author=domme, content="99", channel=service.bot.get_guild(1).get_channel(100))
    asyncio.run(service.process_message(message))
    window = asyncio.run(repo.get_active_recovery_window(1, 100))
    assert window is not None
    repo.windows[window.id] = CountRecoveryWindow(
        id=window.id,
        guild_id=window.guild_id,
        channel_id=window.channel_id,
        failed_user_id=window.failed_user_id,
        failed_user_role=window.failed_user_role,
        required_domme_user_id=window.required_domme_user_id,
        required_domme_id=window.required_domme_id,
        expected_number=window.expected_number,
        attempted_content=window.attempted_content,
        started_at=window.started_at,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        resolved_at=None,
        resolution=None,
        created_at=window.created_at,
    )

    asyncio.run(service.resolve_expired_windows_once())
    assert repo.state.current_number == 0
    assert repo.state.pending_restore is False


def test_sub_recovery_allows_any_domme_regardless_of_claim():
    service, repo, _channel, guild, _domme, domme_alt, sub, claimed_sub = _make_setup()
    repo.state = CountingState(1, 100, 12, 9, True, False, datetime.now(timezone.utc))

    # Unclaimed sub can send to any domme.
    unclaimed_message = _FakeMessageEvent(guild=guild, author=sub, content="99", channel=guild.get_channel(100))
    unclaimed_result = asyncio.run(service.process_message(unclaimed_message))
    assert unclaimed_result is not None
    assert unclaimed_result.reason == "wrong_number_sub_recovery"
    unclaimed_recovered = asyncio.run(
        service.process_send_for_count_rescue(
            SimpleNamespace(
                guild_id=1,
                domme_user_id=domme_alt.id,
                sub_user_id=sub.id,
                sub_name="subby",
                sent_at=datetime.now(timezone.utc),
                is_private=False,
                is_test_send=False,
            )
        )
    )
    assert unclaimed_recovered is True

    # Claimed sub can also send to any domme (claim restriction no longer applies).
    repo.state = CountingState(1, 100, 12, 9, True, False, datetime.now(timezone.utc))
    claimed_message = _FakeMessageEvent(guild=guild, author=claimed_sub, content="99", channel=guild.get_channel(100))
    claimed_result = asyncio.run(service.process_message(claimed_message))
    assert claimed_result is not None
    assert claimed_result.reason == "wrong_number_sub_recovery"
    any_domme_recovered = asyncio.run(
        service.process_send_for_count_rescue(
            SimpleNamespace(
                guild_id=1,
                domme_user_id=domme_alt.id,
                sub_user_id=claimed_sub.id,
                sub_name="claimedsub",
                sent_at=datetime.now(timezone.utc),
                is_private=False,
                is_test_send=False,
            )
        )
    )
    assert any_domme_recovered is True


def test_special_sub_recovery_requires_specific_domme():
    from rob.services.counting_service import _SPECIAL_SUB_REQUIRED_DOMME_USER_ID, _SPECIAL_SUB_USER_ID

    channel = _FakeChannel(channel_id=100)
    special_sub = _FakeMember(_SPECIAL_SUB_USER_ID, [_Role(22, "Sub")], display_name="Special Sub", name="specialsub")
    domme_required = _FakeMember(
        _SPECIAL_SUB_REQUIRED_DOMME_USER_ID, [_Role(33, "Dom/me")], display_name="Required Domme", name="requireddomme"
    )
    domme_other = _FakeMember(20, [_Role(33, "Dom/me")], display_name="Other Domme", name="otherdomme")
    guild = _FakeGuild(1, channel, [special_sub, domme_required, domme_other])
    repo = _FakeCountingRepo()
    service = _service(repo=repo, guild=guild)

    repo.state = CountingState(1, 100, 12, 9, True, False, datetime.now(timezone.utc))
    message = _FakeMessageEvent(guild=guild, author=special_sub, content="99", channel=guild.get_channel(100))
    result = asyncio.run(service.process_message(message))
    assert result is not None
    assert result.reason == "wrong_number_sub_recovery"

    wrong_domme_recovered = asyncio.run(
        service.process_send_for_count_rescue(
            SimpleNamespace(
                guild_id=1,
                domme_user_id=domme_other.id,
                sub_user_id=_SPECIAL_SUB_USER_ID,
                sub_name="specialsub",
                sent_at=datetime.now(timezone.utc),
                is_private=False,
                is_test_send=False,
            )
        )
    )
    assert wrong_domme_recovered is False

    correct_domme_recovered = asyncio.run(
        service.process_send_for_count_rescue(
            SimpleNamespace(
                guild_id=1,
                domme_user_id=_SPECIAL_SUB_REQUIRED_DOMME_USER_ID,
                sub_user_id=_SPECIAL_SUB_USER_ID,
                sub_name="specialsub",
                sent_at=datetime.now(timezone.utc),
                is_private=False,
                is_test_send=False,
            )
        )
    )
    assert correct_domme_recovered is True

def test_sub_recovery_expiry_creates_12h_block_and_blocked_sub_cannot_count():
    service, repo, _channel, guild, _domme, _domme_alt, sub, _claimed_sub = _make_setup()
    repo.state = CountingState(1, 100, 3, 9, True, False, datetime.now(timezone.utc))
    message = _FakeMessageEvent(guild=guild, author=sub, content="99", channel=guild.get_channel(100))
    asyncio.run(service.process_message(message))
    window = asyncio.run(repo.get_active_recovery_window(1, 100))
    assert window is not None
    repo.windows[window.id] = CountRecoveryWindow(
        id=window.id,
        guild_id=window.guild_id,
        channel_id=window.channel_id,
        failed_user_id=window.failed_user_id,
        failed_user_role=window.failed_user_role,
        required_domme_user_id=window.required_domme_user_id,
        required_domme_id=window.required_domme_id,
        expected_number=window.expected_number,
        attempted_content=window.attempted_content,
        started_at=window.started_at,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        resolved_at=None,
        resolution=None,
        created_at=window.created_at,
    )
    asyncio.run(service.resolve_expired_windows_once())
    block = asyncio.run(repo.get_active_block(1, sub.id))
    assert block is not None

    blocked_result = asyncio.run(
        service.process_message(
            _FakeMessageEvent(guild=guild, author=sub, content="4", channel=guild.get_channel(100))
        )
    )
    assert blocked_result is not None
    assert blocked_result.reason == "blocked_sub"

    repo.blocks[(1, sub.id)] = CountBlock(
        id=block.id,
        guild_id=1,
        discord_user_id=sub.id,
        reason=block.reason,
        blocked_until=datetime.now(timezone.utc) - timedelta(seconds=1),
        created_at=block.created_at,
    )
    unblocked_result = asyncio.run(
        service.process_message(
            _FakeMessageEvent(guild=guild, author=sub, content="4", channel=guild.get_channel(100))
        )
    )
    assert unblocked_result is not None
    assert unblocked_result.success is True


def test_recovery_windows_are_restart_safe_and_expiry_resolution_is_idempotent():
    service, repo, _channel, guild, _domme, _domme_alt, _sub, _claimed_sub = _make_setup()
    repo.state = CountingState(1, 100, 15, 8, True, False, datetime.now(timezone.utc))
    now = datetime.now(timezone.utc)
    repo.windows[1] = CountRecoveryWindow(
        id=1,
        guild_id=1,
        channel_id=100,
        failed_user_id=10,
        failed_user_role="sub",
        required_domme_user_id=None,
        required_domme_id=None,
        expected_number=16,
        attempted_content="99",
        started_at=now - timedelta(seconds=20),
        expires_at=now - timedelta(seconds=1),
        resolved_at=None,
        resolution=None,
        created_at=now - timedelta(seconds=20),
    )

    new_service = _service(repo=repo, guild=guild)
    asyncio.run(new_service.resolve_expired_windows_once())
    first_block = asyncio.run(repo.get_active_block(1, 10))
    assert first_block is not None
    first_resolution = repo.windows[1].resolution
    assert first_resolution == "expired_blocked"

    asyncio.run(new_service.resolve_expired_windows_once())
    assert repo.windows[1].resolution == "expired_blocked"


def test_sub_recovery_with_unregistered_send_name_matches_via_registered_name():
    """A send with sub_user_id=None recovers when its name matches the sub's registered Throne username."""
    channel = _FakeChannel(channel_id=100)
    domme = _FakeMember(20, [_Role(33, "Dom/me")], display_name="Miss Adore", name="missadore")
    sub = _FakeMember(10, [_Role(22, "Sub")], display_name="Subby", name="subby")
    guild = _FakeGuild(1, channel, [domme, sub])
    repo = _FakeCountingRepo()
    subs_repo = _FakeSubsRepo(send_names_by_user={sub.id: ["Subby_Throne"]})
    service = _service(repo=repo, guild=guild, subs=subs_repo)

    repo.state = CountingState(1, 100, 12, 9, True, False, datetime.now(timezone.utc))
    message = _FakeMessageEvent(guild=guild, author=sub, content="99", channel=guild.get_channel(100))
    result = asyncio.run(service.process_message(message))
    assert result is not None
    assert result.reason == "wrong_number_sub_recovery"

    recovered = asyncio.run(
        service.process_send_for_count_rescue(
            SimpleNamespace(
                guild_id=1,
                domme_user_id=domme.id,
                sub_user_id=None,
                sub_name="Subby_Throne",
                sent_at=datetime.now(timezone.utc),
                is_private=False,
                is_test_send=False,
            )
        )
    )
    assert recovered is True
    assert repo.state.current_number == 12


def test_sub_recovery_with_unregistered_send_name_fails_without_subs_repo():
    """Without a subs repo, a name-only send cannot recover a sub failure window."""
    channel = _FakeChannel(channel_id=100)
    domme = _FakeMember(20, [_Role(33, "Dom/me")], display_name="Miss Adore", name="missadore")
    sub = _FakeMember(10, [_Role(22, "Sub")], display_name="Subby", name="subby")
    guild = _FakeGuild(1, channel, [domme, sub])
    repo = _FakeCountingRepo()
    service = _service(repo=repo, guild=guild, subs=None)

    repo.state = CountingState(1, 100, 12, 9, True, False, datetime.now(timezone.utc))
    message = _FakeMessageEvent(guild=guild, author=sub, content="99", channel=guild.get_channel(100))
    asyncio.run(service.process_message(message))

    recovered = asyncio.run(
        service.process_send_for_count_rescue(
            SimpleNamespace(
                guild_id=1,
                domme_user_id=domme.id,
                sub_user_id=None,
                sub_name="Subby_Throne",
                sent_at=datetime.now(timezone.utc),
                is_private=False,
                is_test_send=False,
            )
        )
    )
    assert recovered is False


def test_sub_recovery_send_name_match_is_case_insensitive():
    channel = _FakeChannel(channel_id=100)
    domme = _FakeMember(20, [_Role(33, "Dom/me")], display_name="Miss Adore", name="missadore")
    sub = _FakeMember(10, [_Role(22, "Sub")], display_name="Subby", name="subby")
    guild = _FakeGuild(1, channel, [domme, sub])
    repo = _FakeCountingRepo()
    subs_repo = _FakeSubsRepo(send_names_by_user={sub.id: ["SUBBY_THRONE"]})
    service = _service(repo=repo, guild=guild, subs=subs_repo)

    repo.state = CountingState(1, 100, 12, 9, True, False, datetime.now(timezone.utc))
    message = _FakeMessageEvent(guild=guild, author=sub, content="99", channel=guild.get_channel(100))
    asyncio.run(service.process_message(message))

    recovered = asyncio.run(
        service.process_send_for_count_rescue(
            SimpleNamespace(
                guild_id=1,
                domme_user_id=domme.id,
                sub_user_id=None,
                sub_name="subby_throne",
                sent_at=datetime.now(timezone.utc),
                is_private=False,
                is_test_send=False,
            )
        )
    )
    assert recovered is True
