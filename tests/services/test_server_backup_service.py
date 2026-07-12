from __future__ import annotations

import asyncio
import copy
from datetime import datetime, timezone
from types import SimpleNamespace

from rob.services.server_backup_service import ServerBackupService


class _FakeBotState:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get_text(self, key: str) -> str | None:
        return self.values.get(key)

    async def set_value(self, key: str, value: str) -> None:
        self.values[key] = value


class _FakeBackupsRepo:
    def __init__(self) -> None:
        self.backups: list[SimpleNamespace] = []
        self.approvals: dict[int, SimpleNamespace] = {}
        self._backup_seq = 0
        self._approval_seq = 0

    async def create_backup(self, *, guild_id, snapshot, is_baseline=True):
        self._backup_seq += 1
        record = SimpleNamespace(
            id=self._backup_seq,
            guild_id=guild_id,
            snapshot=copy.deepcopy(snapshot),
            is_baseline=is_baseline,
            created_at=datetime.now(timezone.utc),
        )
        self.backups.append(record)
        return record

    async def get_latest_backup(self, guild_id):
        rows = [b for b in self.backups if b.guild_id == guild_id]
        return rows[-1] if rows else None

    async def count_backups(self, guild_id):
        return len([b for b in self.backups if b.guild_id == guild_id])

    async def get_pending_approval(self, guild_id):
        pend = [a for a in self.approvals.values() if a.guild_id == guild_id and a.status == "pending"]
        return pend[-1] if pend else None

    async def get_approval(self, approval_id):
        return self.approvals.get(approval_id)

    async def list_pending_approvals(self):
        return [a for a in self.approvals.values() if a.status == "pending"]

    async def get_last_decided(self, guild_id):
        decided = [
            a for a in self.approvals.values()
            if a.guild_id == guild_id and a.status in {"approved", "rejected", "superseded"}
        ]
        return decided[-1] if decided else None

    async def create_approval(self, *, guild_id, changes, change_signature, pending_snapshot, baseline_backup_id, required_approvals, channel_id=None, message_id=None):
        self._approval_seq += 1
        record = SimpleNamespace(
            id=self._approval_seq,
            guild_id=guild_id,
            status="pending",
            changes=copy.deepcopy(changes),
            change_signature=change_signature,
            pending_snapshot=copy.deepcopy(pending_snapshot),
            baseline_backup_id=baseline_backup_id,
            required_approvals=required_approvals,
            approved_by=[],
            channel_id=channel_id,
            message_id=message_id,
            decided_by_user_id=None,
            decision_reason=None,
            decided_at=None,
        )
        self.approvals[record.id] = record
        return record

    async def set_delivery(self, *, approval_id, channel_id, message_id):
        record = self.approvals.get(approval_id)
        if record is not None:
            record.channel_id = channel_id
            record.message_id = message_id
        return record

    async def set_baseline(self, *, approval_id, baseline_backup_id):
        record = self.approvals.get(approval_id)
        if record is not None:
            record.baseline_backup_id = baseline_backup_id
        return record

    async def add_approver(self, *, approval_id, user_id):
        record = self.approvals.get(approval_id)
        if record is None or record.status != "pending":
            return record
        if user_id not in record.approved_by:
            record.approved_by = [*record.approved_by, user_id]
        return record

    async def finalize(self, *, approval_id, status, decided_by_user_id, decision_reason=None, baseline_backup_id=None):
        record = self.approvals.get(approval_id)
        if record is None or record.status != "pending":
            return None
        record.status = status
        record.decided_by_user_id = decided_by_user_id
        record.decision_reason = decision_reason or record.decision_reason
        if baseline_backup_id is not None:
            record.baseline_backup_id = baseline_backup_id
        record.decided_at = datetime.now(timezone.utc)
        return record


def _service(repo=None, bot_state=None, *, enabled_default=True, required=2, threshold=1):
    return ServerBackupService(
        backups=repo or _FakeBackupsRepo(),
        bot_state=bot_state or _FakeBotState(),
        guild_settings=SimpleNamespace(),
        enabled_default=enabled_default,
        required_approvals=required,
        major_change_threshold=threshold,
    )


def _run(coro):
    return asyncio.run(coro)


def _perm(value):
    return SimpleNamespace(value=value)


def _overwrite(allow, deny):
    return SimpleNamespace(pair=lambda: (_perm(allow), _perm(deny)))


def _make_guild():
    role_mod = SimpleNamespace(
        id=2, name="Mod", permissions=_perm(8), color=_perm(0), hoist=False,
        mentionable=False, position=2, managed=False, is_default=lambda: False,
    )
    role_member = SimpleNamespace(
        id=1, name="Member", permissions=_perm(0), color=_perm(0), hoist=False,
        mentionable=False, position=1, managed=False, is_default=lambda: True,
    )
    channel = SimpleNamespace(
        id=10, name="general", type=SimpleNamespace(name="text"), category_id=None,
        position=0, topic="hi", nsfw=False, slowmode_delay=0, overwrites={},
    )
    return SimpleNamespace(
        id=1, name="VIB", description=None,
        verification_level=SimpleNamespace(name="low"),
        explicit_content_filter=SimpleNamespace(name="disabled"),
        mfa_level=SimpleNamespace(name="none"),
        default_notifications=SimpleNamespace(name="only_mentions"),
        afk_timeout=300, afk_channel=None, system_channel=None, rules_channel=None,
        public_updates_channel=None, premium_tier=0, vanity_url_code=None,
        roles=[role_member, role_mod], channels=[channel],
    )


# -- snapshot + diff -------------------------------------------------------


def test_snapshot_captures_roles_channels_and_settings():
    snap = _service().snapshot_guild(_make_guild())
    assert snap["guild"]["name"] == "VIB"
    assert snap["guild"]["verification_level"] == "low"
    assert snap["roles"]["2"]["permissions"] == 8
    assert snap["channels"]["10"]["name"] == "general"


def test_diff_classifies_major_and_minor():
    S = ServerBackupService
    snap = _service().snapshot_guild(_make_guild())
    new = copy.deepcopy(snap)
    new["guild"]["name"] = "VIB Server"           # minor
    new["guild"]["verification_level"] = "high"   # major
    new["roles"]["2"]["permissions"] = 0          # major
    new["roles"]["1"]["color"] = 99               # minor
    del new["roles"]["1"]                          # also a removal (major) — re-add to isolate
    new["roles"]["1"] = copy.deepcopy(snap["roles"]["1"])
    new["roles"]["1"]["color"] = 99               # minor only
    new["channels"]["10"]["overwrites"] = [{"id": 1, "type": "role", "allow": 0, "deny": 1024}]  # major

    changes = S.diff_snapshots(snap, new)
    majors = {c["detail"] for c in S.major_changes(changes)}
    minors = {c["detail"] for c in changes if not c["major"]}

    assert any("verification level" in d for d in majors)
    assert any("permissions changed" in d for d in majors)
    assert any("permission overwrites changed" in d for d in majors)
    assert any("name" in d for d in minors)
    assert any("appearance/position" in d for d in minors)


def test_role_and_channel_add_remove_are_major():
    S = ServerBackupService
    snap = _service().snapshot_guild(_make_guild())
    new = copy.deepcopy(snap)
    del new["roles"]["2"]                          # role removed -> major
    new["channels"]["11"] = {"name": "secret", "overwrites": [], "type": "text"}  # channel added -> major
    details = {c["detail"]: c["major"] for c in S.diff_snapshots(snap, new)}
    assert details["Role **Mod** was deleted"] is True
    assert details["Channel **#secret** was created"] is True


def test_signature_stable_and_sensitive():
    S = ServerBackupService
    a = [{"category": "role", "kind": "removed", "target": "2", "detail": "Role X deleted", "major": True}]
    b = [{"category": "role", "kind": "added", "target": "3", "detail": "Role Y created", "major": True}]
    assert S.change_signature(a) == S.change_signature(list(a))
    assert S.change_signature(a) != S.change_signature(b)


# -- cycle -----------------------------------------------------------------


def test_cycle_disabled():
    result = _run(_service(enabled_default=False).run_cycle(_make_guild()))
    assert result.action == "disabled"


def test_cycle_first_run_creates_baseline():
    repo = _FakeBackupsRepo()
    result = _run(_service(repo).run_cycle(_make_guild()))
    assert result.action == "created_first"
    assert _run(repo.count_backups(1)) == 1


def test_cycle_no_change():
    repo = _FakeBackupsRepo()
    service = _service(repo)
    guild = _make_guild()
    _run(repo.create_backup(guild_id=1, snapshot=service.snapshot_guild(guild)))
    result = _run(service.run_cycle(guild))
    assert result.action == "no_change"
    assert _run(repo.count_backups(1)) == 1  # no new backup


def test_cycle_minor_change_advances_baseline():
    repo = _FakeBackupsRepo()
    service = _service(repo)
    guild = _make_guild()
    baseline = service.snapshot_guild(guild)
    baseline["guild"]["name"] = "Old Name"  # only a minor difference
    _run(repo.create_backup(guild_id=1, snapshot=baseline))
    result = _run(service.run_cycle(guild))
    assert result.action == "backed_up"
    assert _run(repo.count_backups(1)) == 2


def test_cycle_major_change_opens_approval_and_blocks():
    repo = _FakeBackupsRepo()
    service = _service(repo)
    guild = _make_guild()
    baseline = service.snapshot_guild(guild)
    del baseline["roles"]["2"]  # guild now has an extra role -> major (added)
    _run(repo.create_backup(guild_id=1, snapshot=baseline))

    result = _run(service.run_cycle(guild))
    assert result.action == "needs_approval"
    assert result.approval is not None
    assert result.approval.pending_snapshot["roles"].get("2") is not None

    # While pending, the next cycle is blocked (no new baseline).
    blocked = _run(service.run_cycle(guild))
    assert blocked.action == "blocked"
    assert _run(repo.count_backups(1)) == 1


def test_cycle_below_threshold_major_changes_auto_adopt():
    repo = _FakeBackupsRepo()
    service = _service(repo, threshold=3)
    guild = _make_guild()
    baseline = service.snapshot_guild(guild)
    del baseline["roles"]["2"]                        # role added -> major (1)
    baseline["guild"]["verification_level"] = "high"  # security setting -> major (2)
    _run(repo.create_backup(guild_id=1, snapshot=baseline))

    result = _run(service.run_cycle(guild))

    # Only 2 majors < threshold 3 -> adopted silently as the new baseline.
    assert result.action == "backed_up"
    assert _run(repo.count_backups(1)) == 2
    assert _run(repo.get_pending_approval(1)) is None


def test_cycle_at_threshold_major_changes_open_approval():
    repo = _FakeBackupsRepo()
    service = _service(repo, threshold=3)
    guild = _make_guild()
    baseline = service.snapshot_guild(guild)
    del baseline["roles"]["2"]                        # major (1)
    baseline["guild"]["verification_level"] = "high"  # major (2)
    baseline["channels"]["10"]["overwrites"] = [
        {"id": 1, "type": "role", "allow": 0, "deny": 1024}
    ]                                                  # major (3)
    _run(repo.create_backup(guild_id=1, snapshot=baseline))

    result = _run(service.run_cycle(guild))

    assert result.action == "needs_approval"
    assert len(result.major_changes) >= 3
    assert _run(repo.get_pending_approval(1)) is not None


def test_cycle_suppresses_identical_rejected_change():
    repo = _FakeBackupsRepo()
    service = _service(repo)
    guild = _make_guild()
    baseline = service.snapshot_guild(guild)
    del baseline["roles"]["2"]
    _run(repo.create_backup(guild_id=1, snapshot=baseline))

    first = _run(service.run_cycle(guild))
    assert first.action == "needs_approval"
    _run(service.reject(approval_id=first.approval.id, rejected_by_user_id=42))

    suppressed = _run(service.run_cycle(guild))
    assert suppressed.action == "suppressed"


# -- approval decisions ----------------------------------------------------


def test_two_distinct_moderators_approve_and_promote_baseline():
    repo = _FakeBackupsRepo()
    service = _service(repo, required=2)
    guild = _make_guild()
    baseline = service.snapshot_guild(guild)
    del baseline["roles"]["2"]
    _run(repo.create_backup(guild_id=1, snapshot=baseline))
    approval = _run(service.run_cycle(guild)).approval

    first = _run(service.register_approval(approval_id=approval.id, approver_user_id=111))
    assert first.status == "recorded"
    assert first.remaining == 1

    duplicate = _run(service.register_approval(approval_id=approval.id, approver_user_id=111))
    assert duplicate.status == "duplicate"

    second = _run(service.register_approval(approval_id=approval.id, approver_user_id=222))
    assert second.status == "approved"
    # Pending snapshot promoted to the new baseline backup.
    latest = _run(repo.get_latest_backup(1))
    assert latest.snapshot["roles"].get("2") is not None
    assert _run(repo.get_pending_approval(1)) is None


def test_register_approval_no_spurious_backup_when_finalized_during_race():
    repo = _FakeBackupsRepo()
    service = _service(repo)
    guild = _make_guild()
    baseline = service.snapshot_guild(guild)
    del baseline["roles"]["2"]
    _run(repo.create_backup(guild_id=1, snapshot=baseline))
    approval = _run(service.run_cycle(guild)).approval
    assert _run(repo.count_backups(1)) == 1

    # Simulate the approval being finalized between get_approval and add_approver:
    # add_approver returns the existing (now non-pending) row.
    async def _racing_add_approver(*, approval_id, user_id):
        record = repo.approvals[approval_id]
        record.status = "approved"
        record.approved_by = [999, user_id]
        return record

    repo.add_approver = _racing_add_approver

    decision = _run(service.register_approval(approval_id=approval.id, approver_user_id=111))

    assert decision.status == "gone"
    assert _run(repo.count_backups(1)) == 1  # no extra backup row created


def test_reject_keeps_baseline():
    repo = _FakeBackupsRepo()
    service = _service(repo)
    guild = _make_guild()
    baseline = service.snapshot_guild(guild)
    del baseline["roles"]["2"]
    _run(repo.create_backup(guild_id=1, snapshot=baseline))
    approval = _run(service.run_cycle(guild)).approval

    decision = _run(service.reject(approval_id=approval.id, rejected_by_user_id=9))
    assert decision.status == "rejected"
    assert _run(repo.count_backups(1)) == 1  # baseline unchanged


def test_force_adopt_resolves_dangling_pending():
    repo = _FakeBackupsRepo()
    service = _service(repo)
    guild = _make_guild()
    baseline = service.snapshot_guild(guild)
    del baseline["roles"]["2"]
    _run(repo.create_backup(guild_id=1, snapshot=baseline))
    approval = _run(service.run_cycle(guild)).approval

    resolved = _run(service.force_adopt(approval_id=approval.id, reason="no channel"))
    assert resolved.status == "superseded"
    assert _run(repo.get_pending_approval(1)) is None
    assert _run(repo.count_backups(1)) == 2  # pending snapshot promoted
