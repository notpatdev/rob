from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from rob.opsapi._support import (
    GUILD_CHANNEL_FIELDS as OPS_SERVER_CHANNEL_FIELDS,
    GUILD_ROLE_FIELDS as OPS_SERVER_ROLE_FIELDS,
)
from rob.opsapi import BotOpsServer
from rob.services.server_backup_service import BackupCycleResult
from rob.database.repositories.vib_settings import CHANNEL_FIELD_NAMES, ROLE_FIELD_NAMES
from ops.ops import (
    GUILD_CHANNEL_FIELDS as CLI_CHANNEL_FIELDS,
    GUILD_ROLE_FIELDS as CLI_ROLE_FIELDS,
    GUILD_ROLE_MATCH_TOKENS,
    _find_best_role_match,
    LiveGuildRole,
)


class _Req:
    def __init__(self, *, guild_id="1", payload=None, query=None):
        self._payload = payload if payload is not None else "__error__"
        self.headers: dict[str, str] = {}
        self.query = query or {}
        self.match_info = {"guild_id": str(guild_id)}

    async def json(self):
        if self._payload == "__error__":
            raise ValueError("no json")
        return self._payload

    async def post(self):
        return {}


def _run(coro):
    return asyncio.run(coro)


# -- config / scan field parity -------------------------------------------


def test_new_fields_present_in_all_scan_definitions():
    for field in ("active_role_id", "unverified_role_id", "trial_mod_role_id"):
        assert field in CLI_ROLE_FIELDS
        assert field in OPS_SERVER_ROLE_FIELDS
        assert field in ROLE_FIELD_NAMES
    assert "backup_approval_channel_id" in CLI_CHANNEL_FIELDS
    assert "backup_approval_channel_id" in OPS_SERVER_CHANNEL_FIELDS
    assert "backup_approval_channel_id" in CHANNEL_FIELD_NAMES


def test_scan_distinguishes_active_inactive_and_trial_mod():
    roles = tuple(
        LiveGuildRole(role_id=i, name=name)
        for i, name in enumerate(["Active", "Inactive", "Unverified", "Mods", "Trial Mod"])
    )
    assert _find_best_role_match(roles, "active_role_id").name == "Active"
    assert _find_best_role_match(roles, "inactive_role_id").name == "Inactive"
    assert _find_best_role_match(roles, "unverified_role_id").name == "Unverified"
    assert _find_best_role_match(roles, "mod_role_id").name == "Mods"
    assert _find_best_role_match(roles, "trial_mod_role_id").name == "Trial Mod"
    assert "trialmod" in GUILD_ROLE_MATCH_TOKENS["trial_mod_role_id"]


# -- approval card format --------------------------------------------------


def test_major_change_card_matches_requested_format():
    import discord
    from rob.ui.cards.server_backup import major_change_approval_card

    view = discord.ui.LayoutView(timeout=None)
    rendered = major_change_approval_card(
        change_lines=["Role **Mod** permissions changed"],
        mod_mentions="<@&111> <@&222>",
        approvals=[],
        required_approvals=2,
        view=view,
    )
    text = "\n".join(
        getattr(sub, "content", "")
        for item in rendered.view.children
        for sub in getattr(item, "children", [])
    )
    assert "### Major Server Change Detected!" in text
    assert "Hello <@&111> <@&222>," in text
    assert "the last hourly backup" in text
    assert "* Role **Mod** permissions changed" in text
    assert "at least **2** moderators" in text
    assert "DO NOT ACCEPT THIS IF YOU ARE DOING A REVAMP" in text


# -- bot-ops endpoints -----------------------------------------------------


class _FakeInactivityService:
    def __init__(self):
        self.enabled: dict[int, bool] = {}

    async def is_enabled(self, guild_id):
        return self.enabled.get(guild_id, False)

    async def set_enabled(self, guild_id, value):
        self.enabled[guild_id] = value


class _FakeBackups:
    async def get_latest_backup(self, _guild_id):
        return None

    async def get_pending_approval(self, _guild_id):
        return None


class _FakeBackupService:
    def __init__(self):
        self.enabled: dict[int, bool] = {}
        self.backups = _FakeBackups()
        self.ran: list[int] = []

    async def is_enabled(self, guild_id):
        return self.enabled.get(guild_id, False)

    async def set_enabled(self, guild_id, value):
        self.enabled[guild_id] = value

    async def run_cycle(self, guild):
        self.ran.append(guild.id)
        return BackupCycleResult(action="no_change")


def _server(bot):
    return BotOpsServer(bot=bot, host="127.0.0.1", port=8899, secret=None)


class _FakeInactiveUsersRepo:
    async def list_for_guild(self, _guild_id, *, statuses=None):
        return []


def test_inactivity_toggle_endpoints():
    svc = _FakeInactivityService()
    bot = SimpleNamespace(inactivity_service=svc, inactive_users_repo=_FakeInactiveUsersRepo())
    server = _server(bot)

    resp = _run(server._handle_set_inactivity(_Req(guild_id=5, payload={"enabled": "true"})))
    assert json.loads(resp.text)["enabled"] is True
    assert svc.enabled[5] is True

    resp = _run(server._handle_get_inactivity(_Req(guild_id=5)))
    assert json.loads(resp.text)["enabled"] is True


def test_backup_toggle_and_run_endpoints():
    svc = _FakeBackupService()
    bot = SimpleNamespace(
        server_backup_service=svc,
        get_cog=lambda _name: None,
        get_guild=lambda gid: SimpleNamespace(id=gid),
    )
    server = _server(bot)

    resp = _run(server._handle_set_backup(_Req(guild_id=7, payload={"enabled": "true"})))
    assert json.loads(resp.text)["enabled"] is True

    resp = _run(server._handle_get_backup(_Req(guild_id=7)))
    body = json.loads(resp.text)
    assert body["enabled"] is True and body["pending_approval"] is None

    resp = _run(server._handle_backup_run(_Req(guild_id=7)))
    assert json.loads(resp.text)["action"] == "no_change"
    assert svc.ran == [7]


class _FakeVibRepo:
    def __init__(self):
        self.settings = SimpleNamespace(backup_approval_channel_id=None, active_role_id=None)

    async def set_channel_id(self, _guild_id, field, channel_id):
        setattr(self.settings, field, channel_id)
        return self.settings

    async def set_role_id(self, _guild_id, field, role_id):
        setattr(self.settings, field, role_id)
        return self.settings


class _FakeBackfillService:
    def __init__(self):
        self.backfilled = []
        self.swept = []

    async def backfill_activity_from_history(self, guild, *, days):
        self.backfilled.append((guild.id, days))
        return {"channels_scanned": 3, "users_seen": 5, "users_seeded": 4}

    async def process_guild(self, guild, *, send_notifications, perform_kicks):
        self.swept.append((guild.id, send_notifications, perform_kicks))
        return [object(), object()]  # 2 still inactive


def test_inactivity_backfill_endpoint_seeds_then_safely_sweeps():
    svc = _FakeBackfillService()
    bot = SimpleNamespace(
        inactivity_service=svc,
        get_guild=lambda gid: SimpleNamespace(id=gid),
    )
    server = _server(bot)

    resp = _run(server._handle_inactivity_backfill(_Req(guild_id=9, payload={"days": "7"})))
    body = json.loads(resp.text)

    assert body["members_seeded"] == 4
    assert body["channels_scanned"] == 3
    assert body["still_inactive"] == 2
    assert svc.backfilled == [(9, 7)]
    # The corrective sweep must not DM or kick anyone.
    assert svc.swept == [(9, False, False)]


def test_inactivelist_card_stays_within_discord_text_limit():
    from rob.discord.cogs.inactivity import fit_list_lines
    from rob.ui.cards.inactivity import inactivity_list_card

    # Many long rows (e.g. a big pre-existing Inactive role on the main guild).
    raw = [
        f"- <@{1000000000000000000 + i}> (`{1000000000000000000 + i}`) — kick <t:1750000000:R> (<t:1750000000:F>)"
        for i in range(500)
    ]
    fitted = fit_list_lines(raw, len(raw))
    rendered = inactivity_list_card(fitted, len(raw))

    contents = [
        getattr(sub, "content", "")
        for item in rendered.view.children
        for sub in getattr(item, "children", [])
    ]
    body = max(contents, key=len)
    assert len(body) <= 4000
    assert "more (showing" in body  # truncation note present
    assert len(fitted) < len(raw)


def test_set_channel_and_role_endpoints():
    repo = _FakeVibRepo()
    bot = SimpleNamespace(vib_settings_repo=repo)
    server = _server(bot)

    resp = _run(server._handle_set_guild_channel(_Req(
        guild_id=5,
        payload={"field": "backup_approval_channel_id", "channel_id": "1496237724171112528"},
    )))
    assert json.loads(resp.text)["channel_id"] == 1496237724171112528
    assert repo.settings.backup_approval_channel_id == 1496237724171112528

    resp = _run(server._handle_set_guild_role(_Req(
        guild_id=5, payload={"field": "active_role_id", "role_id": "777"},
    )))
    assert json.loads(resp.text)["role_id"] == 777
    assert repo.settings.active_role_id == 777

    # Unknown field is rejected.
    resp = _run(server._handle_set_guild_channel(_Req(
        guild_id=5, payload={"field": "not_a_real_field", "channel_id": "1"},
    )))
    assert resp.status == 400

    # Clearing a field.
    resp = _run(server._handle_set_guild_channel(_Req(
        guild_id=5, payload={"field": "backup_approval_channel_id", "clear": "true"},
    )))
    assert json.loads(resp.text)["channel_id"] is None
