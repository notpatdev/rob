from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from rob.services.inactivity_service import InactivityService

ACTIVE_ROLE_ID = 100
INACTIVE_ROLE_ID = 99
UNVERIFIED_ROLE_ID = 98


class _FakeBotState:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get_text(self, key: str) -> str | None:
        return self.values.get(key)

    async def set_value(self, key: str, value: str) -> None:
        self.values[key] = value


class _FakeGuildSettingsRepo:
    def __init__(self, **role_ids) -> None:
        self._settings = SimpleNamespace(
            active_role_id=role_ids.get("active_role_id", ACTIVE_ROLE_ID),
            inactive_role_id=role_ids.get("inactive_role_id", INACTIVE_ROLE_ID),
            unverified_role_id=role_ids.get("unverified_role_id"),
        )

    async def get(self, _guild_id: int):
        return self._settings


class _FakeInactiveUsers:
    def __init__(self) -> None:
        self.rows: dict[tuple[int, int], SimpleNamespace] = {}

    async def get(self, guild_id: int, user_id: int):
        return self.rows.get((guild_id, user_id))

    async def start_watching(self, *, guild_id, discord_user_id, inactive_role_assigned_at, remove_at, bot_user_id=None):
        record = SimpleNamespace(
            guild_id=guild_id,
            discord_user_id=discord_user_id,
            inactive_role_assigned_at=inactive_role_assigned_at,
            remove_at=remove_at,
            initial_notice_sent=False,
            final_notice_sent=False,
            status="watching",
        )
        self.rows[(guild_id, discord_user_id)] = record
        return record

    async def mark_initial_notice(self, guild_id: int, user_id: int) -> None:
        record = self.rows.get((guild_id, user_id))
        if record is not None:
            record.initial_notice_sent = True

    async def mark_final_notice(self, guild_id: int, user_id: int) -> None:
        record = self.rows.get((guild_id, user_id))
        if record is not None:
            record.final_notice_sent = True

    async def clear(self, guild_id: int, user_id: int) -> None:
        self.rows.pop((guild_id, user_id), None)

    async def list_for_guild(self, guild_id: int, *, statuses=None):
        return [record for (gid, _uid), record in self.rows.items() if gid == guild_id]


class _FakeMaintenance:
    def __init__(self, enabled: bool = False) -> None:
        self.enabled = enabled

    async def notifications_suppressed(self) -> bool:
        return self.enabled


class _FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class _FakeMember:
    def __init__(self, user_id: int, *, joined_at: datetime | None = None, roles=None) -> None:
        self.id = user_id
        self.bot = False
        self.nick = None
        self.display_name = f"User{user_id}"
        self.name = f"User{user_id}"
        self.joined_at = joined_at
        self.roles = list(roles or [])
        self.dm_messages: list[dict] = []
        self.kicked = False

    async def send(self, content=None, view=None, **kwargs):
        self.dm_messages.append({"content": content, "view": view, **kwargs})

    async def kick(self, *, reason: str):
        del reason
        self.kicked = True

    async def add_roles(self, *roles, reason=None):
        del reason
        for role in roles:
            if all(existing.id != role.id for existing in self.roles):
                self.roles.append(role)

    async def remove_roles(self, *roles, reason=None):
        del reason
        remove_ids = {role.id for role in roles}
        self.roles = [role for role in self.roles if role.id not in remove_ids]

    def has(self, role_id: int) -> bool:
        return any(role.id == role_id for role in self.roles)


class _FakeGuild:
    def __init__(self, guild_id: int, members, *, name: str = "VIB") -> None:
        self.id = guild_id
        self.name = name
        self.members = list(members)
        for member in self.members:
            member.guild = self
        self._roles = {
            ACTIVE_ROLE_ID: _FakeRole(ACTIVE_ROLE_ID),
            INACTIVE_ROLE_ID: _FakeRole(INACTIVE_ROLE_ID),
            UNVERIFIED_ROLE_ID: _FakeRole(UNVERIFIED_ROLE_ID),
        }

    def get_role(self, role_id: int):
        return self._roles.get(role_id)


def _service(*, bot_state, inactive_users, unverified_role_id=None, maintenance=None, **overrides):
    return InactivityService(
        bot_state=bot_state,
        guild_settings=_FakeGuildSettingsRepo(unverified_role_id=unverified_role_id),
        inactive_users=inactive_users,
        enabled_default=overrides.get("enabled_default", False),
        inactive_after_days=overrides.get("inactive_after_days", 7),
        kick_grace_days=overrides.get("kick_grace_days", 14),
        bootstrap_grace_days=overrides.get("bootstrap_grace_days", 21),
        final_notice_days=overrides.get("final_notice_days", 7),
        notice_channel_id=overrides.get("notice_channel_id"),
        maintenance=maintenance,
    )


def _run(coro):
    return asyncio.run(coro)


def _view_text(payload: dict) -> str:
    view = payload.get("view")
    chunks: list[str] = []
    for top_level in getattr(view, "children", []):
        for child in getattr(top_level, "children", []):
            content = getattr(child, "content", None)
            if content:
                chunks.append(str(content))
    return "\n".join(chunks)


def _enable(service, guild_id=1):
    _run(service.set_enabled(guild_id, True))


def test_disabled_by_default_does_nothing():
    member = _FakeMember(10, roles=[_FakeRole(ACTIVE_ROLE_ID)])
    guild = _FakeGuild(1, [member])
    service = _service(bot_state=_FakeBotState(), inactive_users=_FakeInactiveUsers())
    snapshots = _run(service.process_guild(guild, send_notifications=True, perform_kicks=True))
    assert snapshots == []


def test_active_member_keeps_active_role():
    bot_state = _FakeBotState()
    bot_state.values["activity:1:user:10:last_active"] = datetime.now(timezone.utc).isoformat()
    member = _FakeMember(10, roles=[])
    guild = _FakeGuild(1, [member])
    service = _service(bot_state=bot_state, inactive_users=_FakeInactiveUsers())
    _enable(service)

    snapshots = _run(service.process_guild(guild, send_notifications=True, perform_kicks=True))

    assert snapshots == []
    assert member.has(ACTIVE_ROLE_ID)
    assert not member.has(INACTIVE_ROLE_ID)
    assert member.dm_messages == []


def test_inactive_member_swaps_roles_and_sends_first_notice():
    bot_state = _FakeBotState()
    bot_state.values["activity:1:user:10:last_active"] = (
        datetime.now(timezone.utc) - timedelta(days=8)
    ).isoformat()
    # Already bootstrapped: a member going inactive now gets the first notice.
    bot_state.values["inactivity:1:bootstrapped_at"] = (
        datetime.now(timezone.utc) - timedelta(days=1)
    ).isoformat()
    inactive_users = _FakeInactiveUsers()
    member = _FakeMember(10, roles=[_FakeRole(ACTIVE_ROLE_ID)])
    guild = _FakeGuild(1, [member])
    service = _service(bot_state=bot_state, inactive_users=inactive_users)
    _enable(service)

    snapshots = _run(service.process_guild(guild, send_notifications=True, perform_kicks=False))

    assert len(snapshots) == 1
    assert not member.has(ACTIVE_ROLE_ID)
    assert member.has(INACTIVE_ROLE_ID)
    assert (1, 10) in inactive_users.rows
    assert len(member.dm_messages) == 1
    rendered = _view_text(member.dm_messages[0])
    assert "inactive" in rendered.lower()
    assert "<t:" in rendered


def test_bootstrap_grandfathers_without_first_notice_blast():
    bot_state = _FakeBotState()
    bot_state.values["activity:1:user:10:last_active"] = (
        datetime.now(timezone.utc) - timedelta(days=8)
    ).isoformat()
    # No bootstrapped marker -> this is the first run after enabling.
    inactive_users = _FakeInactiveUsers()
    member = _FakeMember(10, roles=[_FakeRole(ACTIVE_ROLE_ID)])
    guild = _FakeGuild(1, [member])
    service = _service(bot_state=bot_state, inactive_users=inactive_users)
    _enable(service)

    _run(service.process_guild(guild, send_notifications=True, perform_kicks=True))

    # Flagged inactive + on the watchlist, but no day-one first-notice DM.
    assert member.has(INACTIVE_ROLE_ID)
    assert member.dm_messages == []
    assert member.kicked is False
    assert inactive_users.rows[(1, 10)].initial_notice_sent is True


def test_unverified_member_parked_inactive_without_countdown():
    bot_state = _FakeBotState()
    inactive_users = _FakeInactiveUsers()
    member = _FakeMember(10, roles=[_FakeRole(UNVERIFIED_ROLE_ID), _FakeRole(ACTIVE_ROLE_ID)])
    guild = _FakeGuild(1, [member])
    service = _service(bot_state=bot_state, inactive_users=inactive_users, unverified_role_id=UNVERIFIED_ROLE_ID)
    _enable(service)

    snapshots = _run(service.process_guild(guild, send_notifications=True, perform_kicks=True))

    assert snapshots == []
    assert member.has(INACTIVE_ROLE_ID)
    assert not member.has(ACTIVE_ROLE_ID)
    assert (1, 10) not in inactive_users.rows  # never on the kick countdown
    assert member.dm_messages == []
    assert member.kicked is False


def test_reactivation_restores_active_role_and_clears_watch():
    bot_state = _FakeBotState()
    bot_state.values["activity:1:user:10:last_active"] = datetime.now(timezone.utc).isoformat()
    inactive_users = _FakeInactiveUsers()
    now = datetime.now(timezone.utc)
    inactive_users.rows[(1, 10)] = SimpleNamespace(
        guild_id=1, discord_user_id=10, inactive_role_assigned_at=now - timedelta(days=8),
        remove_at=now + timedelta(days=7), initial_notice_sent=True, final_notice_sent=False, status="watching",
    )
    member = _FakeMember(10, roles=[_FakeRole(INACTIVE_ROLE_ID)])
    guild = _FakeGuild(1, [member])
    service = _service(bot_state=bot_state, inactive_users=inactive_users)
    _enable(service)

    snapshots = _run(service.process_guild(guild, send_notifications=False, perform_kicks=False))

    assert snapshots == []
    assert member.has(ACTIVE_ROLE_ID)
    assert not member.has(INACTIVE_ROLE_ID)
    assert (1, 10) not in inactive_users.rows


def test_final_notice_sent_within_window():
    bot_state = _FakeBotState()
    bot_state.values["activity:1:user:10:last_active"] = (
        datetime.now(timezone.utc) - timedelta(days=20)
    ).isoformat()
    bot_state.values["inactivity:1:bootstrapped_at"] = (
        datetime.now(timezone.utc) - timedelta(days=20)
    ).isoformat()
    inactive_users = _FakeInactiveUsers()
    now = datetime.now(timezone.utc)
    inactive_users.rows[(1, 10)] = SimpleNamespace(
        guild_id=1, discord_user_id=10, inactive_role_assigned_at=now - timedelta(days=14),
        remove_at=now + timedelta(days=6), initial_notice_sent=True, final_notice_sent=False, status="notice_sent",
    )
    member = _FakeMember(10, roles=[_FakeRole(INACTIVE_ROLE_ID)])
    guild = _FakeGuild(1, [member])
    service = _service(bot_state=bot_state, inactive_users=inactive_users)
    _enable(service)

    _run(service.process_guild(guild, send_notifications=True, perform_kicks=False))

    assert len(member.dm_messages) == 1
    assert inactive_users.rows[(1, 10)].final_notice_sent is True
    assert "Last call" in _view_text(member.dm_messages[0])


def test_kick_when_expired():
    bot_state = _FakeBotState()
    bot_state.values["activity:1:user:10:last_active"] = (
        datetime.now(timezone.utc) - timedelta(days=30)
    ).isoformat()
    bot_state.values["inactivity:1:bootstrapped_at"] = (
        datetime.now(timezone.utc) - timedelta(days=30)
    ).isoformat()
    inactive_users = _FakeInactiveUsers()
    now = datetime.now(timezone.utc)
    inactive_users.rows[(1, 10)] = SimpleNamespace(
        guild_id=1, discord_user_id=10, inactive_role_assigned_at=now - timedelta(days=21),
        remove_at=now - timedelta(minutes=5), initial_notice_sent=True, final_notice_sent=True, status="final_notice_sent",
    )
    member = _FakeMember(10, roles=[_FakeRole(INACTIVE_ROLE_ID)])
    guild = _FakeGuild(1, [member])
    service = _service(bot_state=bot_state, inactive_users=inactive_users)
    _enable(service)

    snapshots = _run(service.process_guild(guild, send_notifications=False, perform_kicks=True))

    assert snapshots == []
    assert member.kicked is True
    assert (1, 10) not in inactive_users.rows


def test_maintenance_suppresses_dms_and_kicks_but_still_swaps_roles():
    bot_state = _FakeBotState()
    bot_state.values["activity:1:user:10:last_active"] = (
        datetime.now(timezone.utc) - timedelta(days=30)
    ).isoformat()
    bot_state.values["inactivity:1:bootstrapped_at"] = (
        datetime.now(timezone.utc) - timedelta(days=30)
    ).isoformat()
    inactive_users = _FakeInactiveUsers()
    now = datetime.now(timezone.utc)
    inactive_users.rows[(1, 10)] = SimpleNamespace(
        guild_id=1, discord_user_id=10, inactive_role_assigned_at=now - timedelta(days=21),
        remove_at=now - timedelta(minutes=5), initial_notice_sent=False, final_notice_sent=False, status="watching",
    )
    member = _FakeMember(10, roles=[_FakeRole(ACTIVE_ROLE_ID)])
    guild = _FakeGuild(1, [member])
    service = _service(bot_state=bot_state, inactive_users=inactive_users, maintenance=_FakeMaintenance(enabled=True))
    _enable(service)

    snapshots = _run(service.process_guild(guild, send_notifications=True, perform_kicks=True))

    assert len(snapshots) == 1
    assert member.dm_messages == []
    assert member.kicked is False
    assert member.has(INACTIVE_ROLE_ID)


def test_register_member_activity_reactivates_inactive_member_instantly():
    bot_state = _FakeBotState()
    inactive_users = _FakeInactiveUsers()
    now = datetime.now(timezone.utc)
    inactive_users.rows[(1, 10)] = SimpleNamespace(
        guild_id=1, discord_user_id=10, inactive_role_assigned_at=now - timedelta(days=8),
        remove_at=now + timedelta(days=7), initial_notice_sent=True, final_notice_sent=False, status="watching",
    )
    member = _FakeMember(10, roles=[_FakeRole(INACTIVE_ROLE_ID)])
    guild = _FakeGuild(1, [member])
    service = _service(bot_state=bot_state, inactive_users=inactive_users)
    _enable(service)

    _run(service.register_member_activity(guild, member))

    assert member.has(ACTIVE_ROLE_ID)
    assert not member.has(INACTIVE_ROLE_ID)
    assert (1, 10) not in inactive_users.rows
    assert bot_state.values.get("activity:1:user:10:last_active") is not None


def test_register_member_activity_records_but_does_not_touch_active_member():
    bot_state = _FakeBotState()
    inactive_users = _FakeInactiveUsers()
    member = _FakeMember(10, roles=[_FakeRole(ACTIVE_ROLE_ID)])
    guild = _FakeGuild(1, [member])
    service = _service(bot_state=bot_state, inactive_users=inactive_users)
    _enable(service)

    _run(service.register_member_activity(guild, member))

    assert bot_state.values.get("activity:1:user:10:last_active") is not None
    assert member.has(ACTIVE_ROLE_ID)
    assert not member.has(INACTIVE_ROLE_ID)


def test_register_member_activity_does_not_reactivate_unverified():
    bot_state = _FakeBotState()
    inactive_users = _FakeInactiveUsers()
    member = _FakeMember(10, roles=[_FakeRole(INACTIVE_ROLE_ID), _FakeRole(UNVERIFIED_ROLE_ID)])
    guild = _FakeGuild(1, [member])
    service = _service(bot_state=bot_state, inactive_users=inactive_users, unverified_role_id=UNVERIFIED_ROLE_ID)
    _enable(service)

    _run(service.register_member_activity(guild, member))

    assert member.has(INACTIVE_ROLE_ID)
    assert not member.has(ACTIVE_ROLE_ID)


MAIN_CHANNEL_ID = 555


def test_inactive_member_cannot_reclaim_active_outside_main_channel():
    bot_state = _FakeBotState()
    inactive_users = _FakeInactiveUsers()
    member = _FakeMember(10, roles=[_FakeRole(INACTIVE_ROLE_ID)])
    guild = _FakeGuild(1, [member])
    service = _service(
        bot_state=bot_state, inactive_users=inactive_users, notice_channel_id=MAIN_CHANNEL_ID
    )
    _enable(service)

    _run(service.register_member_activity(guild, member, channel_id=777))

    # No activity stamp either, or the next sweep would flip them back.
    assert member.has(INACTIVE_ROLE_ID)
    assert not member.has(ACTIVE_ROLE_ID)
    assert bot_state.values.get("activity:1:user:10:last_active") is None


def test_inactive_member_reclaims_active_in_main_channel():
    bot_state = _FakeBotState()
    inactive_users = _FakeInactiveUsers()
    member = _FakeMember(10, roles=[_FakeRole(INACTIVE_ROLE_ID)])
    guild = _FakeGuild(1, [member])
    service = _service(
        bot_state=bot_state, inactive_users=inactive_users, notice_channel_id=MAIN_CHANNEL_ID
    )
    _enable(service)

    _run(service.register_member_activity(guild, member, channel_id=MAIN_CHANNEL_ID))

    assert member.has(ACTIVE_ROLE_ID)
    assert not member.has(INACTIVE_ROLE_ID)
    assert bot_state.values.get("activity:1:user:10:last_active") is not None


def test_inactive_member_reclaims_active_in_thread_of_main_channel():
    bot_state = _FakeBotState()
    inactive_users = _FakeInactiveUsers()
    member = _FakeMember(10, roles=[_FakeRole(INACTIVE_ROLE_ID)])
    guild = _FakeGuild(1, [member])
    service = _service(
        bot_state=bot_state, inactive_users=inactive_users, notice_channel_id=MAIN_CHANNEL_ID
    )
    _enable(service)

    _run(
        service.register_member_activity(
            guild, member, channel_id=888, thread_parent_id=MAIN_CHANNEL_ID
        )
    )

    assert member.has(ACTIVE_ROLE_ID)
    assert not member.has(INACTIVE_ROLE_ID)


def test_active_member_gets_no_credit_outside_main_channel():
    bot_state = _FakeBotState()
    inactive_users = _FakeInactiveUsers()
    member = _FakeMember(10, roles=[_FakeRole(ACTIVE_ROLE_ID)])
    guild = _FakeGuild(1, [member])
    service = _service(
        bot_state=bot_state, inactive_users=inactive_users, notice_channel_id=MAIN_CHANNEL_ID
    )
    _enable(service)

    _run(service.register_member_activity(guild, member, channel_id=777))

    # Activity elsewhere stamps nothing; a member who never chats in main drifts inactive.
    assert bot_state.values.get("activity:1:user:10:last_active") is None
    assert member.has(ACTIVE_ROLE_ID)  # roles untouched by the ignored event


def test_active_member_gets_credit_in_main_channel():
    bot_state = _FakeBotState()
    inactive_users = _FakeInactiveUsers()
    member = _FakeMember(10, roles=[_FakeRole(ACTIVE_ROLE_ID)])
    guild = _FakeGuild(1, [member])
    service = _service(
        bot_state=bot_state, inactive_users=inactive_users, notice_channel_id=MAIN_CHANNEL_ID
    )
    _enable(service)

    _run(service.register_member_activity(guild, member, channel_id=MAIN_CHANNEL_ID))

    assert bot_state.values.get("activity:1:user:10:last_active") is not None
    assert member.has(ACTIVE_ROLE_ID)


def test_backfill_scans_only_main_channel_when_configured():
    bot_state = _FakeBotState()
    now = datetime.now(timezone.utc)
    main = _FakeHistoryChannel([_FakeHistoryMessage(10, now - timedelta(days=1))])
    main.id = MAIN_CHANNEL_ID
    side = _FakeHistoryChannel([_FakeHistoryMessage(11, now - timedelta(days=1))])
    side.id = 777
    thread_of_main = _FakeHistoryChannel([_FakeHistoryMessage(12, now - timedelta(days=1))])
    thread_of_main.id = 888
    thread_of_main.parent_id = MAIN_CHANNEL_ID
    guild = SimpleNamespace(
        id=1, me=None, text_channels=[main, side], threads=[thread_of_main], members=[]
    )
    service = _service(
        bot_state=bot_state,
        inactive_users=_FakeInactiveUsers(),
        notice_channel_id=MAIN_CHANNEL_ID,
    )

    result = _run(service.backfill_activity_from_history(guild, days=7))

    # Main + its thread scanned; the side channel is ignored.
    assert result["channels_scanned"] == 2
    assert _run(service.get_last_activity(1, 10)) is not None
    assert _run(service.get_last_activity(1, 12)) is not None
    assert _run(service.get_last_activity(1, 11)) is None


def test_no_main_channel_configured_reactivates_from_anywhere():
    bot_state = _FakeBotState()
    inactive_users = _FakeInactiveUsers()
    member = _FakeMember(10, roles=[_FakeRole(INACTIVE_ROLE_ID)])
    guild = _FakeGuild(1, [member])
    service = _service(bot_state=bot_state, inactive_users=inactive_users)
    _enable(service)

    _run(service.register_member_activity(guild, member, channel_id=777))

    assert member.has(ACTIVE_ROLE_ID)
    assert not member.has(INACTIVE_ROLE_ID)


def test_counts_as_activity_helper():
    service = _service(
        bot_state=_FakeBotState(),
        inactive_users=_FakeInactiveUsers(),
        notice_channel_id=MAIN_CHANNEL_ID,
    )
    assert service.counts_as_activity(MAIN_CHANNEL_ID)
    assert service.counts_as_activity(888, MAIN_CHANNEL_ID)
    assert not service.counts_as_activity(777)
    assert not service.counts_as_activity(None)

    unrestricted = _service(bot_state=_FakeBotState(), inactive_users=_FakeInactiveUsers())
    assert unrestricted.counts_as_activity(None)
    assert unrestricted.counts_as_activity(777)


def test_register_member_activity_records_when_disabled_without_role_changes():
    bot_state = _FakeBotState()
    inactive_users = _FakeInactiveUsers()
    member = _FakeMember(10, roles=[_FakeRole(INACTIVE_ROLE_ID)])
    guild = _FakeGuild(1, [member])
    service = _service(bot_state=bot_state, inactive_users=inactive_users)
    # Not enabled: activity still recorded (history), but no reactivation.

    _run(service.register_member_activity(guild, member))

    assert bot_state.values.get("activity:1:user:10:last_active") is not None
    assert member.has(INACTIVE_ROLE_ID)
    assert not member.has(ACTIVE_ROLE_ID)


def test_sync_member_now_parks_unverified_instantly():
    bot_state = _FakeBotState()
    inactive_users = _FakeInactiveUsers()
    member = _FakeMember(10, roles=[_FakeRole(ACTIVE_ROLE_ID), _FakeRole(UNVERIFIED_ROLE_ID)])
    guild = _FakeGuild(1, [member])
    service = _service(bot_state=bot_state, inactive_users=inactive_users, unverified_role_id=UNVERIFIED_ROLE_ID)
    _enable(service)

    _run(service.sync_member_now(guild, member))

    assert member.has(INACTIVE_ROLE_ID)
    assert not member.has(ACTIVE_ROLE_ID)
    assert (1, 10) not in inactive_users.rows


def test_sync_member_now_activates_verified_instantly_and_stamps_activity():
    bot_state = _FakeBotState()
    inactive_users = _FakeInactiveUsers()
    now = datetime.now(timezone.utc)
    inactive_users.rows[(1, 10)] = SimpleNamespace(
        guild_id=1, discord_user_id=10, inactive_role_assigned_at=now, remove_at=now + timedelta(days=5),
        initial_notice_sent=True, final_notice_sent=False, status="watching",
    )
    member = _FakeMember(10, roles=[_FakeRole(INACTIVE_ROLE_ID)])  # was parked, just verified
    guild = _FakeGuild(1, [member])
    service = _service(bot_state=bot_state, inactive_users=inactive_users, unverified_role_id=UNVERIFIED_ROLE_ID)
    _enable(service)

    _run(service.sync_member_now(guild, member))

    assert member.has(ACTIVE_ROLE_ID)
    assert not member.has(INACTIVE_ROLE_ID)
    assert (1, 10) not in inactive_users.rows
    assert bot_state.values.get("activity:1:user:10:last_active") is not None


def test_sync_member_now_noop_when_disabled():
    bot_state = _FakeBotState()
    inactive_users = _FakeInactiveUsers()
    member = _FakeMember(10, roles=[_FakeRole(INACTIVE_ROLE_ID)])
    guild = _FakeGuild(1, [member])
    service = _service(bot_state=bot_state, inactive_users=inactive_users)
    # not enabled

    _run(service.sync_member_now(guild, member))

    assert member.has(INACTIVE_ROLE_ID)
    assert not member.has(ACTIVE_ROLE_ID)


def test_list_inactive_members_shows_role_holders_countdown_first():
    bot_state = _FakeBotState()
    inactive_users = _FakeInactiveUsers()
    now = datetime.now(timezone.utc)
    on_countdown = _FakeMember(10, roles=[_FakeRole(INACTIVE_ROLE_ID)])
    parked = _FakeMember(11, roles=[_FakeRole(INACTIVE_ROLE_ID)])  # role but no countdown
    inactive_users.rows[(1, 10)] = SimpleNamespace(
        guild_id=1, discord_user_id=10, inactive_role_assigned_at=now,
        remove_at=now + timedelta(days=5), initial_notice_sent=True, final_notice_sent=False, status="watching",
    )
    guild = _FakeGuild(1, [on_countdown, parked])
    guild._roles[INACTIVE_ROLE_ID].members = [parked, on_countdown]  # deliberately unsorted
    service = _service(bot_state=bot_state, inactive_users=inactive_users)

    rows = _run(service.list_inactive_members(guild))

    # Soonest scheduled kick first, parked (no countdown) last.
    assert [member.id for member, _ in rows] == [10, 11]
    assert rows[0][1] is not None
    assert rows[1][1] is None


def test_requires_active_and_inactive_roles_configured():
    bot_state = _FakeBotState()
    service = InactivityService(
        bot_state=bot_state,
        guild_settings=_FakeGuildSettingsRepo(active_role_id=None),
        inactive_users=_FakeInactiveUsers(),
        enabled_default=True,
        inactive_after_days=7,
        kick_grace_days=14,
        bootstrap_grace_days=21,
        final_notice_days=7,
        notice_channel_id=None,
    )
    member = _FakeMember(10, roles=[])
    guild = _FakeGuild(1, [member])
    snapshots = _run(service.process_guild(guild, send_notifications=True, perform_kicks=True))
    assert snapshots == []


def test_bootstrap_uses_longer_grace_and_marks_bootstrapped():
    bot_state = _FakeBotState()
    bot_state.values["activity:1:user:10:last_active"] = (
        datetime.now(timezone.utc) - timedelta(days=8)
    ).isoformat()
    inactive_users = _FakeInactiveUsers()
    member = _FakeMember(10, roles=[_FakeRole(ACTIVE_ROLE_ID)])
    guild = _FakeGuild(1, [member])
    service = _service(bot_state=bot_state, inactive_users=inactive_users)
    _enable(service)

    _run(service.process_guild(guild, send_notifications=False, perform_kicks=True))

    record = inactive_users.rows[(1, 10)]
    days_until_removal = (record.remove_at - datetime.now(timezone.utc)).days
    assert days_until_removal >= 20  # bootstrap grace (21d), not the 14d normal grace
    assert "inactivity:1:bootstrapped_at" in bot_state.values
    assert member.kicked is False  # nobody is kicked on the bootstrap run


class _FakeHistoryMessage:
    def __init__(self, author_id, created_at, *, bot=False):
        self.author = SimpleNamespace(id=author_id, bot=bot)
        self.created_at = created_at


class _FakeHistoryChannel:
    def __init__(self, messages):
        self._messages = messages

    def history(self, *, after=None, limit=None):
        msgs = self._messages

        async def _gen():
            for m in msgs:
                if after is None or m.created_at > after:
                    yield m

        return _gen()


def test_backfill_activity_from_history_seeds_recent_authors():
    bot_state = _FakeBotState()
    service = _service(bot_state=bot_state, inactive_users=_FakeInactiveUsers())
    now = datetime.now(timezone.utc)
    channel = _FakeHistoryChannel([
        _FakeHistoryMessage(10, now - timedelta(days=2)),            # active in window
        _FakeHistoryMessage(11, now - timedelta(days=30)),           # too old (filtered by after=)
        _FakeHistoryMessage(12, now - timedelta(days=1), bot=True),  # bot, skipped
    ])
    guild = SimpleNamespace(id=1, me=None, text_channels=[channel], threads=[], members=[])

    result = _run(service.backfill_activity_from_history(guild, days=7))

    assert result["users_seeded"] == 1
    assert _run(service.get_last_activity(1, 10)) is not None
    assert _run(service.get_last_activity(1, 11)) is None  # outside the window
    assert _run(service.get_last_activity(1, 12)) is None  # bot


def test_backfill_only_overwrites_with_newer_timestamp():
    bot_state = _FakeBotState()
    service = _service(bot_state=bot_state, inactive_users=_FakeInactiveUsers())
    now = datetime.now(timezone.utc)
    # Existing record is newer than the history message; backfill must not regress it.
    bot_state.values["activity:1:user:10:last_active"] = (now - timedelta(hours=1)).isoformat()
    channel = _FakeHistoryChannel([_FakeHistoryMessage(10, now - timedelta(days=3))])
    guild = SimpleNamespace(id=1, me=None, text_channels=[channel], threads=[], members=[])

    result = _run(service.backfill_activity_from_history(guild, days=7))

    assert result["users_seeded"] == 0  # not overwritten with the older time
    stored = _run(service.get_last_activity(1, 10))
    assert (now - stored) < timedelta(hours=2)


PROTECTED_USER_ID = 1455563825393832095


def test_protected_user_kept_active_and_never_inactive():
    from rob.config.guilds import is_protected_user

    assert is_protected_user(PROTECTED_USER_ID) is True
    assert is_protected_user(999) is False

    bot_state = _FakeBotState()
    now = datetime.now(timezone.utc)
    # Old activity + already bootstrapped: a normal member here would be inactive.
    bot_state.values[f"activity:1:user:{PROTECTED_USER_ID}:last_active"] = (now - timedelta(days=30)).isoformat()
    bot_state.values["inactivity:1:bootstrapped_at"] = (now - timedelta(days=1)).isoformat()
    inactive_users = _FakeInactiveUsers()
    member = _FakeMember(PROTECTED_USER_ID, roles=[_FakeRole(INACTIVE_ROLE_ID)])
    guild = _FakeGuild(1, [member])
    service = _service(bot_state=bot_state, inactive_users=inactive_users)
    _enable(service)

    snapshots = _run(service.process_guild(guild, send_notifications=True, perform_kicks=True))

    assert member.has(ACTIVE_ROLE_ID)
    assert not member.has(INACTIVE_ROLE_ID)
    assert member.kicked is False
    assert member.dm_messages == []
    assert (1, PROTECTED_USER_ID) not in inactive_users.rows
    assert all(snapshot.member.id != PROTECTED_USER_ID for snapshot in snapshots)


def test_protected_user_kept_active_in_sync_even_if_unverified():
    bot_state = _FakeBotState()
    member = _FakeMember(PROTECTED_USER_ID, roles=[_FakeRole(INACTIVE_ROLE_ID), _FakeRole(UNVERIFIED_ROLE_ID)])
    guild = _FakeGuild(1, [member])
    service = _service(bot_state=bot_state, inactive_users=_FakeInactiveUsers(), unverified_role_id=UNVERIFIED_ROLE_ID)
    _enable(service)

    _run(service.sync_member_now(guild, member))

    assert member.has(ACTIVE_ROLE_ID)
    assert not member.has(INACTIVE_ROLE_ID)


# -- bots are ignored by the inactivity system ----------------------------


def test_bot_members_are_ignored_by_the_sweep():
    bot_state = _FakeBotState()
    now = datetime.now(timezone.utc)
    # Old activity + already bootstrapped: a human here would be flagged inactive.
    bot_state.values["activity:1:user:10:last_active"] = (now - timedelta(days=30)).isoformat()
    bot_state.values["inactivity:1:bootstrapped_at"] = (now - timedelta(days=1)).isoformat()
    inactive_users = _FakeInactiveUsers()
    member = _FakeMember(10, roles=[_FakeRole(ACTIVE_ROLE_ID)])
    member.bot = True
    guild = _FakeGuild(1, [member])
    service = _service(bot_state=bot_state, inactive_users=inactive_users)
    _enable(service)

    snapshots = _run(service.process_guild(guild, send_notifications=True, perform_kicks=True))

    assert snapshots == []
    assert member.kicked is False
    assert member.dm_messages == []
    assert not member.has(INACTIVE_ROLE_ID)  # untouched
    assert (1, 10) not in inactive_users.rows


def test_sync_member_now_ignores_bots():
    bot_state = _FakeBotState()
    member = _FakeMember(10, roles=[])  # a bot joining the server
    member.bot = True
    guild = _FakeGuild(1, [member])
    service = _service(bot_state=bot_state, inactive_users=_FakeInactiveUsers())
    _enable(service)

    _run(service.sync_member_now(guild, member))

    assert not member.has(ACTIVE_ROLE_ID)
    assert not member.has(INACTIVE_ROLE_ID)


def test_register_member_activity_ignores_bots():
    bot_state = _FakeBotState()
    member = _FakeMember(10, roles=[_FakeRole(INACTIVE_ROLE_ID)])
    member.bot = True
    guild = _FakeGuild(1, [member])
    service = _service(bot_state=bot_state, inactive_users=_FakeInactiveUsers())
    _enable(service)

    _run(service.register_member_activity(guild, member))

    assert bot_state.values.get("activity:1:user:10:last_active") is None
    assert member.has(INACTIVE_ROLE_ID)
    assert not member.has(ACTIVE_ROLE_ID)


# -- /afk exemption -------------------------------------------------------


def test_set_member_afk_persists_until_and_returns_it():
    bot_state = _FakeBotState()
    service = _service(bot_state=bot_state, inactive_users=_FakeInactiveUsers())

    until = _run(service.set_member_afk(1, 10))

    assert _run(service.get_afk_until(1, 10)) is not None
    delta = until - datetime.now(timezone.utc)
    assert timedelta(days=6) < delta <= timedelta(days=7)  # a week


def test_afk_member_exempt_from_inactivity_sweep():
    bot_state = _FakeBotState()
    now = datetime.now(timezone.utc)
    # Old activity + bootstrapped: normally inactive, but they ran /afk.
    bot_state.values["activity:1:user:10:last_active"] = (now - timedelta(days=30)).isoformat()
    bot_state.values["inactivity:1:bootstrapped_at"] = (now - timedelta(days=1)).isoformat()
    bot_state.values["inactivity:1:user:10:afk_until"] = (now + timedelta(days=5)).isoformat()
    inactive_users = _FakeInactiveUsers()
    member = _FakeMember(10, roles=[_FakeRole(ACTIVE_ROLE_ID)])
    guild = _FakeGuild(1, [member])
    service = _service(bot_state=bot_state, inactive_users=inactive_users)
    _enable(service)

    snapshots = _run(service.process_guild(guild, send_notifications=True, perform_kicks=True))

    assert snapshots == []
    assert member.has(ACTIVE_ROLE_ID)
    assert not member.has(INACTIVE_ROLE_ID)
    assert member.kicked is False
    assert member.dm_messages == []
    assert (1, 10) not in inactive_users.rows


def test_afk_expired_member_is_flagged_inactive_normally():
    bot_state = _FakeBotState()
    now = datetime.now(timezone.utc)
    bot_state.values["activity:1:user:10:last_active"] = (now - timedelta(days=30)).isoformat()
    bot_state.values["inactivity:1:bootstrapped_at"] = (now - timedelta(days=1)).isoformat()
    bot_state.values["inactivity:1:user:10:afk_until"] = (now - timedelta(days=1)).isoformat()  # lapsed
    inactive_users = _FakeInactiveUsers()
    member = _FakeMember(10, roles=[_FakeRole(ACTIVE_ROLE_ID)])
    guild = _FakeGuild(1, [member])
    service = _service(bot_state=bot_state, inactive_users=inactive_users)
    _enable(service)

    snapshots = _run(service.process_guild(guild, send_notifications=True, perform_kicks=False))

    assert len(snapshots) == 1
    assert member.has(INACTIVE_ROLE_ID)
    assert not member.has(ACTIVE_ROLE_ID)


def test_afk_command_marks_only_the_author_exempt():
    from rob.discord.cogs.inactivity import InactivityCog

    bot_state = _FakeBotState()
    service = _service(bot_state=bot_state, inactive_users=_FakeInactiveUsers())
    sent: dict = {}

    async def _send_message(**kwargs):
        sent.update(kwargs)

    interaction = SimpleNamespace(
        guild=SimpleNamespace(id=1),
        user=SimpleNamespace(id=10),
        response=SimpleNamespace(send_message=_send_message),
    )
    cog = SimpleNamespace(bot=SimpleNamespace(inactivity_service=service))

    _run(InactivityCog.afk.callback(cog, interaction))

    assert _run(service.get_afk_until(1, 10)) is not None
    assert _run(service.get_afk_until(1, 11)) is None
    assert sent.get("ephemeral") is True
    assert sent.get("view") is not None
