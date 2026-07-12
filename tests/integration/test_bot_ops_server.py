from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import discord
from rob.services.bot_ops_server import BotOpsServer


class _FakeRequest:
    def __init__(
        self,
        *,
        payload: dict | None = None,
        form_payload: dict | None = None,
        headers: dict[str, str] | None = None,
        query: dict[str, str] | None = None,
    ):
        self._payload = payload or {}
        self._form_payload = form_payload or {}
        self.headers = headers or {}
        self.query = query or {}
        self.match_info: dict[str, str] = {}

    async def json(self):
        if self._payload == "__error__":
            raise ValueError("json parse failed")
        return self._payload

    async def post(self):
        return _FakeForm(self._form_payload)


class _FakeForm(dict):
    def getall(self, key):
        value = self[key]
        if isinstance(value, list):
            return value
        return [value]


class _FakeSendQueue:
    def __init__(self):
        self.notified: list[int] = []

    async def notify_send(self, send_id: int) -> None:
        self.notified.append(send_id)


class _FakeRegistrationService:
    def __init__(self):
        self.calls: list[dict] = []

    async def register_sub(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(sub=SimpleNamespace(id=7, discord_user_id=kwargs["discord_user_id"]), send_names=tuple(kwargs["send_names"]))


class _FakeSendChangeRequestService:
    def __init__(self):
        self.calls: list[dict] = []

    async def create_send_add_request(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            id=11,
            action="send_add",
            status="pending",
            domme_user_id=555,
        )

    async def create_send_update_request(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            id=12,
            action="send_update",
            status="pending",
            domme_user_id=555,
            target_send_id=kwargs["send_id"],
        )


class _FakeVibSettingsRepo:
    def __init__(self):
        self.channel_calls: list[tuple[int, str, int | None]] = []
        self.role_calls: list[tuple[int, str, int | None]] = []
        self.settings = SimpleNamespace(
            guild_id=99,
            registration_channel_id=None,
            leaderboard_channel_id=22,
            send_track_channel_id=None,
            counting_channel_id=None,
            report_channel_id=None,
            warn_log_channel_id=None,
            domme_role_id=None,
            sub_role_id=None,
            mod_role_id=77,
            inactive_role_id=None,
        )

    async def get(self, guild_id: int):
        assert guild_id == 99
        return self.settings

    async def set_channel_id(self, guild_id: int, field_name: str, channel_id: int | None):
        self.channel_calls.append((guild_id, field_name, channel_id))
        setattr(self.settings, field_name, channel_id)
        return self.settings

    async def set_role_id(self, guild_id: int, field_name: str, role_id: int | None):
        self.role_calls.append((guild_id, field_name, role_id))
        setattr(self.settings, field_name, role_id)
        return self.settings


class _FakeBotSettingsRepo:
    def __init__(self):
        self.values: dict[str, str] = {}

    async def get_text(self, key: str):
        return self.values.get(key)

    async def set_value(self, key: str, value: str):
        self.values[key] = value


class _FakeDommesRepo:
    def __init__(self, dommes: list[SimpleNamespace]):
        self.dommes = dommes

    async def list_for_guild(self, guild_id: int):
        return [domme for domme in self.dommes if domme.guild_id == guild_id]

    async def count(self, guild_id: int):
        return len(await self.list_for_guild(guild_id))

    async def get_by_handle(self, guild_id: int, throne_handle: str):
        for domme in await self.list_for_guild(guild_id):
            if getattr(domme, "throne_handle", "").casefold() == throne_handle.casefold():
                return domme
        return None

    async def get_by_user_id(self, guild_id: int, discord_user_id: int):
        for domme in await self.list_for_guild(guild_id):
            if domme.discord_user_id == discord_user_id:
                return domme
        return None


class _FakeSendsRepoSummary:
    async def count_for_guild(self, guild_id: int):
        assert guild_id == 99
        return 207

    async def total_cents_for_guild(self, guild_id: int):
        assert guild_id == 99
        return 647264


class _FakeLeaderboardsRepoSummary:
    async def get_summary(self, guild_id: int):
        assert guild_id == 99
        return SimpleNamespace(send_count=207, total_cents=647264)

    async def get_message(self, guild_id: int, message_key: str):
        assert guild_id == 99
        if message_key == "leaderboard":
            return SimpleNamespace(channel_id=22, message_id=111)
        if message_key == "leaderboard_stats":
            return SimpleNamespace(channel_id=22, message_id=222)
        return None


class _FakeCountingService:
    async def get_or_create_state(self, guild_id: int):
        assert guild_id == 99
        return SimpleNamespace(current_number=4321, channel_id=44, is_enabled=True)


class _FakeMaintenanceService:
    def __init__(self):
        self.enabled = False
        self.reason = None

    async def get_state(self):
        return SimpleNamespace(enabled=self.enabled, reason=self.reason)

    async def enable(self, *, reason: str | None):
        self.enabled = True
        self.reason = reason

    async def disable(self):
        self.enabled = False
        self.reason = None


class _FakeRegistrationServiceReissue:
    def __init__(self):
        self.calls: list[tuple[int, int]] = []

    async def reissue_domme_webhook(self, *, guild_id: int, discord_user_id: int):
        self.calls.append((guild_id, discord_user_id))
        return SimpleNamespace(
            domme=SimpleNamespace(id=88, throne_handle="missadore"),
            webhook_url="https://throne.robthebot.com/webhook/creator/secret",
        )


class _FakeDmUser:
    def __init__(self):
        self.messages: list[dict] = []

    async def send(self, **kwargs):
        self.messages.append(kwargs)
        return SimpleNamespace(id=77, channel=SimpleNamespace(id=66))


class _FakeOpsChannel:
    def __init__(self, channel_id: int):
        self.id = channel_id
        self.messages: list[dict] = []

    async def send(self, **kwargs):
        self.messages.append(kwargs)
        return SimpleNamespace(id=len(self.messages))


class _FakeOpsGuild:
    def __init__(self, *, channels: dict[int, _FakeOpsChannel], members: dict[int, SimpleNamespace] | None = None):
        self.id = 99
        self.name = "Rob Test Server"
        self._channels = channels
        self._members = members or {}

    def get_channel(self, channel_id: int):
        return self._channels.get(channel_id)

    async def fetch_channel(self, channel_id: int):
        return self._channels.get(channel_id)

    def get_member(self, user_id: int):
        return self._members.get(user_id)


def _make_fake_guild():
    leaderboard = MagicMock(spec=discord.TextChannel)
    leaderboard.id = 22
    leaderboard.name = "leaderboard"

    send_tracker = MagicMock(spec=discord.TextChannel)
    send_tracker.id = 33
    send_tracker.name = "send-tracker"

    counting = MagicMock(spec=discord.TextChannel)
    counting.id = 44
    counting.name = "counting"

    return SimpleNamespace(
        id=99,
        name="Rob Test Server",
        channels=[leaderboard, send_tracker, counting],
        roles=[
            SimpleNamespace(id=77, name="Moderator"),
            SimpleNamespace(id=88, name="Dom/me"),
            SimpleNamespace(id=99, name="Sub"),
            SimpleNamespace(id=1, name="@everyone"),
        ],
    )


def test_bot_ops_process_send_endpoint_enqueues_specific_send():
    send_queue = _FakeSendQueue()
    bot = SimpleNamespace(send_queue_service=send_queue, user=SimpleNamespace(id=123))
    server = BotOpsServer(bot=bot, host="127.0.0.1", port=8811, secret="shared")
    request = _FakeRequest(
        payload={"send_id": 42, "guild_id": 99},
        headers={"X-Rob-Ops-Secret": "shared"},
    )

    response = asyncio.run(server._handle_process_send(request))

    assert response.status == 200
    assert send_queue.notified == [42]
    body = json.loads(response.text)
    assert body == {"ok": True, "queued": True, "send_id": 42, "guild_id": 99}


def test_bot_ops_process_send_endpoint_requires_secret():
    send_queue = _FakeSendQueue()
    bot = SimpleNamespace(send_queue_service=send_queue, user=SimpleNamespace(id=123))
    server = BotOpsServer(bot=bot, host="127.0.0.1", port=8811, secret="shared")
    request = _FakeRequest(payload={"send_id": 42}, headers={})

    response = asyncio.run(server._handle_process_send(request))

    assert response.status == 403
    assert send_queue.notified == []


def test_bot_ops_rejects_wrong_secret():
    bot = SimpleNamespace(user=SimpleNamespace(id=123))
    server = BotOpsServer(bot=bot, host="127.0.0.1", port=8811, secret="shared")
    request = _FakeRequest(headers={"X-Rob-Ops-Secret": "nope"})

    response = asyncio.run(server._handle_health(request))

    assert response.status == 403


def test_bot_ops_without_secret_allows_loopback():
    bot = SimpleNamespace(user=SimpleNamespace(id=123))
    server = BotOpsServer(bot=bot, host="127.0.0.1", port=8811, secret=None)
    request = _FakeRequest(headers={})

    response = asyncio.run(server._handle_health(request))

    assert response.status == 200


def test_bot_ops_without_secret_denies_non_loopback_bind():
    # Fail closed: no secret + non-loopback bind must not serve unauthenticated.
    send_queue = _FakeSendQueue()
    bot = SimpleNamespace(send_queue_service=send_queue, user=SimpleNamespace(id=123))
    server = BotOpsServer(bot=bot, host="0.0.0.0", port=8811, secret=None)
    request = _FakeRequest(payload={"send_id": 42}, headers={})

    response = asyncio.run(server._handle_process_send(request))

    assert response.status == 403
    assert send_queue.notified == []


def test_bot_ops_add_sub_accepts_form_payload_send_names_string():
    registration_service = _FakeRegistrationService()
    bot = SimpleNamespace(
        registration_service=registration_service,
        user=SimpleNamespace(id=123),
    )
    server = BotOpsServer(bot=bot, host="127.0.0.1", port=8811, secret="shared")
    request = _FakeRequest(
        payload="__error__",
        form_payload={
            "discord_user_id": "42",
            "send_names": "alpha, beta, gamma",
        },
        headers={"X-Rob-Ops-Secret": "shared"},
    )
    request.match_info["guild_id"] = "99"

    response = asyncio.run(server._handle_add_sub(request))

    assert response.status == 200
    assert registration_service.calls == [
        {
            "guild_id": 99,
            "discord_user_id": 42,
            "send_names": ["alpha", "beta", "gamma"],
        }
    ]


def test_bot_ops_send_add_request_endpoint_uses_approval_service():
    approval_service = _FakeSendChangeRequestService()
    bot = SimpleNamespace(
        send_change_request_service=approval_service,
        user=SimpleNamespace(id=123),
    )
    server = BotOpsServer(bot=bot, host="127.0.0.1", port=8811, secret="shared")
    request = _FakeRequest(
        payload={
            "domme_lookup": "missadore",
            "amount": "25.50",
            "sub_name": "pat",
            "requested_by": "rob@test",
        },
        headers={"X-Rob-Ops-Secret": "shared"},
    )
    request.match_info["guild_id"] = "99"

    response = asyncio.run(server._handle_request_send_add(request))

    assert response.status == 200
    assert approval_service.calls == [
        {
            "guild_id": 99,
            "domme_lookup": "missadore",
            "amount_cents": 2550,
            "sub_name": "pat",
            "requested_by": "rob@test",
            "currency": "USD",
            "method": "manual",
            "note": None,
        }
    ]


def test_bot_ops_send_update_request_endpoint_uses_approval_service():
    approval_service = _FakeSendChangeRequestService()
    bot = SimpleNamespace(
        send_change_request_service=approval_service,
        user=SimpleNamespace(id=123),
    )
    server = BotOpsServer(bot=bot, host="127.0.0.1", port=8811, secret="shared")
    request = _FakeRequest(
        payload={
            "domme_lookup": "missadore",
            "send_id": "321",
            "amount": "18.75",
            "message_id": "654321",
            "reason": "Price correction",
            "requested_by": "Pat",
        },
        headers={"X-Rob-Ops-Secret": "shared"},
    )
    request.match_info["guild_id"] = "99"

    response = asyncio.run(server._handle_request_send_update(request))

    assert response.status == 200
    assert approval_service.calls == [
        {
            "guild_id": 99,
            "domme_lookup": "missadore",
            "send_id": 321,
            "amount_cents": 1875,
            "message_id": 654321,
            "reason": "Price correction",
            "requested_by": "Pat",
            "currency": "USD",
        }
    ]


def test_bot_ops_guild_scan_returns_suggestions():
    vib_settings_repo = _FakeVibSettingsRepo()
    guild = _make_fake_guild()
    bot = SimpleNamespace(
        get_guild=lambda guild_id: guild if guild_id == 99 else None,
        vib_settings_repo=vib_settings_repo,
        user=SimpleNamespace(id=123),
    )
    server = BotOpsServer(bot=bot, host="127.0.0.1", port=8811, secret="shared")
    request = _FakeRequest(headers={"X-Rob-Ops-Secret": "shared"})
    request.match_info["guild_id"] = "99"

    response = asyncio.run(server._handle_guild_scan(request))

    assert response.status == 200
    body = json.loads(response.text)
    send_tracker_entry = next(
        entry for entry in body["channel_matches"] if entry["field"] == "send_track_channel_id"
    )
    domme_role_entry = next(
        entry for entry in body["role_matches"] if entry["field"] == "domme_role_id"
    )
    assert send_tracker_entry["suggested"]["id"] == 33
    assert send_tracker_entry["current"]["id"] is None
    assert domme_role_entry["suggested"]["id"] == 88


def test_bot_ops_guild_auto_apply_updates_selected_suggestions():
    vib_settings_repo = _FakeVibSettingsRepo()
    guild = _make_fake_guild()
    bot = SimpleNamespace(
        get_guild=lambda guild_id: guild if guild_id == 99 else None,
        vib_settings_repo=vib_settings_repo,
        user=SimpleNamespace(id=123),
    )
    server = BotOpsServer(bot=bot, host="127.0.0.1", port=8811, secret="shared")
    request = _FakeRequest(
        payload={"options": "send_track_channel_id,domme_role_id"},
        headers={"X-Rob-Ops-Secret": "shared"},
    )
    request.match_info["guild_id"] = "99"

    response = asyncio.run(server._handle_apply_guild_scan(request))

    assert response.status == 200
    assert vib_settings_repo.channel_calls == [(99, "send_track_channel_id", 33)]
    assert vib_settings_repo.role_calls == [(99, "domme_role_id", 88)]
    body = json.loads(response.text)
    assert [entry["field"] for entry in body["applied"]] == [
        "send_track_channel_id",
        "domme_role_id",
    ]


def test_bot_ops_migration_audit_endpoint_returns_text_summary():
    dommes = [
        SimpleNamespace(
            guild_id=99,
            discord_user_id=555,
            throne_handle="missadore",
            tracking_status="active",
            profile_status="active",
            webhook_connected_at=None,
            last_successful_event_at=None,
        )
    ]
    async def _list_subs(guild_id: int):
        assert guild_id == 99
        return [SimpleNamespace(id=1)]

    bot = SimpleNamespace(
        get_guild=lambda guild_id: _make_fake_guild() if guild_id == 99 else None,
        dommes_repo=_FakeDommesRepo(dommes),
        subs_repo=SimpleNamespace(list_for_guild=_list_subs),
        sends_repo=_FakeSendsRepoSummary(),
        leaderboards_repo=_FakeLeaderboardsRepoSummary(),
        counting_service=_FakeCountingService(),
        maintenance_service=_FakeMaintenanceService(),
        bot_settings_repo=_FakeBotSettingsRepo(),
        user=SimpleNamespace(id=123),
    )
    server = BotOpsServer(bot=bot, host="127.0.0.1", port=8811, secret="shared")
    request = _FakeRequest(headers={"X-Rob-Ops-Secret": "shared"}, query={"format": "text"})
    request.match_info["guild_id"] = "99"

    response = asyncio.run(server._handle_migration_audit(request))

    assert response.status == 200
    assert "Migration Audit" in response.text
    assert "Tracked Sends: 207" in response.text
    assert "Webhook Reissue Pending: 1" in response.text


def test_bot_ops_webhook_preview_endpoint_marks_already_reissued_users():
    dommes = [
        SimpleNamespace(
            guild_id=99,
            discord_user_id=555,
            throne_handle="missadore",
            tracking_status="active",
            profile_status="active",
            webhook_connected_at=None,
            last_successful_event_at=None,
        ),
        SimpleNamespace(
            guild_id=99,
            discord_user_id=777,
            throne_handle="mistress",
            tracking_status="active",
            profile_status="active",
            webhook_connected_at=object(),
            last_successful_event_at=object(),
        ),
    ]
    settings_repo = _FakeBotSettingsRepo()
    settings_repo.values["migration:webhook_reissue:99:777"] = "2026-05-30T00:00:00+00:00"
    bot = SimpleNamespace(
        get_guild=lambda guild_id: _make_fake_guild() if guild_id == 99 else None,
        dommes_repo=_FakeDommesRepo(dommes),
        bot_settings_repo=settings_repo,
        user=SimpleNamespace(id=123),
    )
    server = BotOpsServer(bot=bot, host="127.0.0.1", port=8811, secret="shared")
    request = _FakeRequest(headers={"X-Rob-Ops-Secret": "shared"})
    request.match_info["guild_id"] = "99"

    response = asyncio.run(server._handle_webhook_reissue_preview(request))

    body = json.loads(response.text)
    assert response.status == 200
    assert body["pending_reissue_count"] == 1
    assert body["already_reissued_count"] == 1
    skipped = next(row for row in body["dommes"] if row["discord_user_id"] == 777)
    assert skipped["will_send"] is False


def test_bot_ops_webhook_preview_uses_fallback_label_when_handle_missing():
    dommes = [
        SimpleNamespace(
            guild_id=99,
            discord_user_id=555,
            throne_handle=None,
            throne_url="https://throne.com/missadore",
            public_display_name=None,
            throne_creator_id="creator_1",
            tracking_status="active",
            profile_status="active",
            webhook_connected_at=None,
            last_successful_event_at=None,
        )
    ]
    bot = SimpleNamespace(
        get_guild=lambda guild_id: _make_fake_guild() if guild_id == 99 else None,
        dommes_repo=_FakeDommesRepo(dommes),
        bot_settings_repo=_FakeBotSettingsRepo(),
        user=SimpleNamespace(id=123),
    )
    server = BotOpsServer(bot=bot, host="127.0.0.1", port=8811, secret="shared")
    request = _FakeRequest(headers={"X-Rob-Ops-Secret": "shared"}, query={"format": "text"})
    request.match_info["guild_id"] = "99"

    response = asyncio.run(server._handle_webhook_reissue_preview(request))

    assert response.status == 200
    assert "handle=missadore" in response.text


def test_bot_ops_webhook_send_rotates_and_marks_reissue_sent():
    dm_user = _FakeDmUser()
    dommes = [
        SimpleNamespace(
            guild_id=99,
            discord_user_id=555,
            throne_handle="missadore",
            tracking_status="active",
            profile_status="active",
            webhook_connected_at=None,
            last_successful_event_at=None,
        )
    ]
    settings_repo = _FakeBotSettingsRepo()
    registration_service = _FakeRegistrationServiceReissue()
    async def _get_settings(guild_id: int):
        assert guild_id == 99
        return SimpleNamespace(send_track_channel_id=33)

    bot = SimpleNamespace(
        get_guild=lambda guild_id: _make_fake_guild() if guild_id == 99 else None,
        get_user=lambda user_id: dm_user if user_id == 555 else None,
        guild_settings_repo=SimpleNamespace(get=_get_settings),
        registration_service=registration_service,
        dommes_repo=_FakeDommesRepo(dommes),
        bot_settings_repo=settings_repo,
        user=SimpleNamespace(id=123),
    )
    server = BotOpsServer(bot=bot, host="127.0.0.1", port=8811, secret="shared")
    request = _FakeRequest(
        payload={"all": "true"},
        headers={"X-Rob-Ops-Secret": "shared"},
        query={"format": "text"},
    )
    request.match_info["guild_id"] = "99"

    response = asyncio.run(server._handle_webhook_reissue_send(request))

    assert response.status == 200
    assert registration_service.calls == [(99, 555)]
    assert dm_user.messages
    assert settings_repo.values["migration:webhook_reissue:99:555"]
    assert "Delivered: 1" in response.text


def test_bot_ops_webhook_refresh_resolves_handle_and_sends_dm():
    dm_user = _FakeDmUser()
    dommes = [
        SimpleNamespace(
            guild_id=99,
            discord_user_id=555,
            throne_handle="missadore",
            public_display_name=None,
            tracking_status="active",
            profile_status="active",
            webhook_connected_at=None,
            last_successful_event_at=None,
        )
    ]
    settings_repo = _FakeBotSettingsRepo()
    registration_service = _FakeRegistrationServiceReissue()

    async def _get_settings(guild_id: int):
        assert guild_id == 99
        return SimpleNamespace(send_track_channel_id=33)

    bot = SimpleNamespace(
        get_guild=lambda guild_id: _make_fake_guild() if guild_id == 99 else None,
        get_user=lambda user_id: dm_user if user_id == 555 else None,
        guild_settings_repo=SimpleNamespace(get=_get_settings),
        registration_service=registration_service,
        dommes_repo=_FakeDommesRepo(dommes),
        bot_settings_repo=settings_repo,
        user=SimpleNamespace(id=123),
    )
    server = BotOpsServer(bot=bot, host="127.0.0.1", port=8811, secret="shared")
    request = _FakeRequest(
        form_payload={"domme_lookup": "missadore"},
        payload="__error__",
        headers={"X-Rob-Ops-Secret": "shared"},
        query={"format": "text"},
    )
    request.match_info["guild_id"] = "99"

    response = asyncio.run(server._handle_webhook_reissue_refresh(request))

    assert response.status == 200
    assert registration_service.calls == [(99, 555)]
    assert dm_user.messages
    assert "Webhook URL Refreshed" in response.text
    assert "Throne Handle: missadore" in response.text


def test_bot_ops_maintenance_accepts_form_payload():
    maintenance_service = _FakeMaintenanceService()
    bot = SimpleNamespace(maintenance_service=maintenance_service, user=SimpleNamespace(id=123))
    server = BotOpsServer(bot=bot, host="127.0.0.1", port=8811, secret="shared")
    request = _FakeRequest(
        payload="__error__",
        form_payload={"enabled": "true", "reason": "Deploying update"},
        headers={"X-Rob-Ops-Secret": "shared"},
        query={"format": "text"},
    )

    response = asyncio.run(server._handle_set_maintenance(request))

    assert response.status == 200
    assert maintenance_service.enabled is True
    assert maintenance_service.reason == "Deploying update"
    assert "Enabled: yes" in response.text
    assert "Reason: Deploying update" in response.text
