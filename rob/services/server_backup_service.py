"""Hourly server-backup snapshots + major-change moderator approval.

Every cycle Rob snapshots a guild's structure (roles, channels, and core server
settings) and diffs it against the last stored baseline:

* No changes              -> nothing to store; the baseline still represents the guild.
* Minor / a few majors    -> store the new snapshot as the new baseline silently.
* A *batch* of major changes (``major_change_threshold`` or more) -> pause backups
  and open a moderator approval. Backups stay paused until ``required_approvals``
  distinct moderators approve, at which point the pending snapshot is promoted to
  the new baseline. A rejected change keeps the old baseline; the identical change
  is not re-prompted until it changes.

"Major" covers deletions, permission changes, and structural changes (added /
removed roles or channels, changed role permissions, changed channel permission
overwrites, and security-relevant server settings). Renames, reorders, colours,
topics, and slowmode are minor. A small number of major changes (a single new
channel, one permission tweak) is treated as routine and adopted without a
prompt; only a *bunch* of them at once (a likely revamp) trips the approval gate.

The snapshot/diff helpers operate on plain dicts so they are easy to test; only
:meth:`snapshot_guild` touches a live ``discord.Guild``.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import discord

from rob.database.repositories.bot_state import BotStateRepository
from rob.database.repositories.guild_settings import GuildSettingsRepository
from rob.models import ServerBackup, ServerBackupApproval
from rob.database.repositories.server_backups import ServerBackupsRepository

log = logging.getLogger(__name__)

# Guild settings whose change is treated as major (security / structural).
_MAJOR_GUILD_FIELDS = {
    "verification_level",
    "explicit_content_filter",
    "mfa_level",
    "default_notifications",
}
_GUILD_FIELD_LABELS = {
    "name": "name",
    "description": "description",
    "verification_level": "verification level",
    "explicit_content_filter": "explicit content filter",
    "mfa_level": "2FA requirement",
    "default_notifications": "default notifications",
    "afk_timeout": "AFK timeout",
    "afk_channel_id": "AFK channel",
    "system_channel_id": "system channel",
    "rules_channel_id": "rules channel",
    "public_updates_channel_id": "community updates channel",
    "premium_tier": "boost tier",
    "vanity_url_code": "vanity invite",
}


@dataclass
class BackupCycleResult:
    action: str
    approval: ServerBackupApproval | None = None
    backup: ServerBackup | None = None
    changes: list[dict[str, Any]] = field(default_factory=list)
    major_changes: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ApprovalDecision:
    status: str  # "recorded" | "approved" | "rejected" | "duplicate" | "gone"
    approval: ServerBackupApproval | None
    remaining: int = 0


class ServerBackupService:
    def __init__(
        self,
        *,
        backups: ServerBackupsRepository,
        bot_state: BotStateRepository,
        guild_settings: GuildSettingsRepository,
        enabled_default: bool,
        required_approvals: int,
        major_change_threshold: int = 1,
    ) -> None:
        self.backups = backups
        self.bot_state = bot_state
        self.guild_settings = guild_settings
        self.enabled_default = enabled_default
        self.required_approvals = max(1, required_approvals)
        # Approval only fires once this many major changes pile up since the last
        # baseline; fewer than this are adopted silently like minor edits.
        self.major_change_threshold = max(1, major_change_threshold)

    # -- enable flag ----------------------------------------------------------

    def _enabled_key(self, guild_id: int) -> str:
        return f"server_backup:{guild_id}:enabled"

    async def is_enabled(self, guild_id: int) -> bool:
        value = await self.bot_state.get_text(self._enabled_key(guild_id))
        if value is None:
            return self.enabled_default
        return value.strip().lower() in {"1", "true", "yes", "on"}

    async def set_enabled(self, guild_id: int, enabled: bool) -> None:
        await self.bot_state.set_value(self._enabled_key(guild_id), "true" if enabled else "false")

    # -- snapshot -------------------------------------------------------------

    @staticmethod
    def _perm_value(permissions: Any) -> int:
        value = getattr(permissions, "value", permissions)
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @classmethod
    def _overwrites(cls, channel: Any) -> list[dict[str, Any]]:
        overwrites = getattr(channel, "overwrites", {}) or {}
        rows: list[dict[str, Any]] = []
        for target, overwrite in overwrites.items():
            try:
                allow, deny = overwrite.pair()
            except AttributeError:
                continue
            target_type = "role" if isinstance(target, discord.Role) else "member"
            rows.append(
                {
                    "id": int(getattr(target, "id", 0)),
                    "type": target_type,
                    "allow": cls._perm_value(allow),
                    "deny": cls._perm_value(deny),
                }
            )
        rows.sort(key=lambda row: (row["type"], row["id"]))
        return rows

    @staticmethod
    def _enum_name(value: Any) -> Any:
        if value is None:
            return None
        return getattr(value, "name", str(value))

    def snapshot_guild(self, guild: discord.Guild) -> dict[str, Any]:
        guild_settings = {
            "name": getattr(guild, "name", None),
            "description": getattr(guild, "description", None),
            "verification_level": self._enum_name(getattr(guild, "verification_level", None)),
            "explicit_content_filter": self._enum_name(getattr(guild, "explicit_content_filter", None)),
            "mfa_level": self._enum_name(getattr(guild, "mfa_level", None)),
            "default_notifications": self._enum_name(getattr(guild, "default_notifications", None)),
            "afk_timeout": getattr(guild, "afk_timeout", None),
            "afk_channel_id": getattr(getattr(guild, "afk_channel", None), "id", None),
            "system_channel_id": getattr(getattr(guild, "system_channel", None), "id", None),
            "rules_channel_id": getattr(getattr(guild, "rules_channel", None), "id", None),
            "public_updates_channel_id": getattr(getattr(guild, "public_updates_channel", None), "id", None),
            "premium_tier": getattr(guild, "premium_tier", None),
            "vanity_url_code": getattr(guild, "vanity_url_code", None),
        }

        roles: dict[str, Any] = {}
        for role in getattr(guild, "roles", []):
            roles[str(role.id)] = {
                "name": role.name,
                "permissions": self._perm_value(getattr(role, "permissions", 0)),
                "color": self._perm_value(getattr(role, "color", 0)),
                "hoist": bool(getattr(role, "hoist", False)),
                "mentionable": bool(getattr(role, "mentionable", False)),
                "position": int(getattr(role, "position", 0)),
                "managed": bool(getattr(role, "managed", False)),
                "is_default": bool(getattr(role, "is_default", lambda: False)()) if callable(getattr(role, "is_default", None)) else False,
            }

        channels: dict[str, Any] = {}
        for channel in getattr(guild, "channels", []):
            channels[str(channel.id)] = {
                "name": getattr(channel, "name", None),
                "type": self._enum_name(getattr(channel, "type", None)),
                "category_id": getattr(channel, "category_id", None),
                "position": int(getattr(channel, "position", 0)),
                "topic": getattr(channel, "topic", None),
                "nsfw": bool(getattr(channel, "nsfw", False)),
                "slowmode_delay": getattr(channel, "slowmode_delay", None),
                "overwrites": self._overwrites(channel),
            }

        return {"guild": guild_settings, "roles": roles, "channels": channels}

    # -- diff -----------------------------------------------------------------

    @classmethod
    def diff_snapshots(cls, old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []
        changes.extend(cls._diff_guild(old.get("guild", {}) or {}, new.get("guild", {}) or {}))
        changes.extend(cls._diff_roles(old.get("roles", {}) or {}, new.get("roles", {}) or {}))
        changes.extend(cls._diff_channels(old.get("channels", {}) or {}, new.get("channels", {}) or {}))
        return changes

    @staticmethod
    def _change(category: str, kind: str, target: str, detail: str, *, major: bool) -> dict[str, Any]:
        return {"category": category, "kind": kind, "target": target, "detail": detail, "major": major}

    @classmethod
    def _diff_guild(cls, old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []
        for field_name in sorted(set(old) | set(new)):
            before = old.get(field_name)
            after = new.get(field_name)
            if before == after:
                continue
            label = _GUILD_FIELD_LABELS.get(field_name, field_name)
            major = field_name in _MAJOR_GUILD_FIELDS
            changes.append(
                cls._change(
                    "guild",
                    "modified",
                    field_name,
                    f"Server setting **{label}** changed (`{before}` → `{after}`)",
                    major=major,
                )
            )
        return changes

    @classmethod
    def _diff_roles(cls, old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []
        for role_id in new.keys() - old.keys():
            name = new[role_id].get("name", role_id)
            changes.append(cls._change("role", "added", role_id, f"Role **{name}** was created", major=True))
        for role_id in old.keys() - new.keys():
            name = old[role_id].get("name", role_id)
            changes.append(cls._change("role", "removed", role_id, f"Role **{name}** was deleted", major=True))
        for role_id in old.keys() & new.keys():
            before, after = old[role_id], new[role_id]
            name = after.get("name", role_id)
            if before.get("permissions") != after.get("permissions"):
                changes.append(
                    cls._change("role", "modified", role_id, f"Role **{name}** permissions changed", major=True)
                )
            if before.get("name") != after.get("name"):
                changes.append(
                    cls._change(
                        "role",
                        "renamed",
                        role_id,
                        f"Role renamed **{before.get('name')}** → **{after.get('name')}**",
                        major=False,
                    )
                )
            if any(before.get(key) != after.get(key) for key in ("color", "hoist", "mentionable", "position")):
                changes.append(
                    cls._change("role", "modified", role_id, f"Role **{name}** appearance/position changed", major=False)
                )
        return changes

    @classmethod
    def _diff_channels(cls, old: dict[str, Any], new: dict[str, Any]) -> list[dict[str, Any]]:
        changes: list[dict[str, Any]] = []
        for channel_id in new.keys() - old.keys():
            name = new[channel_id].get("name", channel_id)
            changes.append(cls._change("channel", "added", channel_id, f"Channel **#{name}** was created", major=True))
        for channel_id in old.keys() - new.keys():
            name = old[channel_id].get("name", channel_id)
            changes.append(cls._change("channel", "removed", channel_id, f"Channel **#{name}** was deleted", major=True))
        for channel_id in old.keys() & new.keys():
            before, after = old[channel_id], new[channel_id]
            name = after.get("name", channel_id)
            if before.get("overwrites") != after.get("overwrites"):
                changes.append(
                    cls._change(
                        "channel",
                        "modified",
                        channel_id,
                        f"Channel **#{name}** permission overwrites changed",
                        major=True,
                    )
                )
            if before.get("name") != after.get("name"):
                changes.append(
                    cls._change(
                        "channel",
                        "renamed",
                        channel_id,
                        f"Channel renamed **#{before.get('name')}** → **#{after.get('name')}**",
                        major=False,
                    )
                )
            if any(before.get(key) != after.get(key) for key in ("topic", "nsfw", "slowmode_delay", "category_id", "position", "type")):
                changes.append(
                    cls._change("channel", "modified", channel_id, f"Channel **#{name}** settings changed", major=False)
                )
        return changes

    @staticmethod
    def major_changes(changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [change for change in changes if change.get("major")]

    @staticmethod
    def change_signature(changes: list[dict[str, Any]]) -> str:
        material = sorted(
            (change["category"], change["kind"], change["target"], change["detail"])
            for change in changes
        )
        digest = hashlib.sha256(json.dumps(material, sort_keys=True).encode("utf-8")).hexdigest()
        return digest

    # -- cycle ----------------------------------------------------------------

    async def run_cycle(self, guild: discord.Guild) -> BackupCycleResult:
        guild_id = guild.id
        if not await self.is_enabled(guild_id):
            return BackupCycleResult(action="disabled")

        snapshot = self.snapshot_guild(guild)

        pending = await self.backups.get_pending_approval(guild_id)
        if pending is not None:
            return BackupCycleResult(action="blocked", approval=pending)

        baseline = await self.backups.get_latest_backup(guild_id)
        if baseline is None:
            backup = await self.backups.create_backup(guild_id=guild_id, snapshot=snapshot)
            return BackupCycleResult(action="created_first", backup=backup)

        changes = self.diff_snapshots(baseline.snapshot, snapshot)
        major = self.major_changes(changes)

        # Only a *batch* of major changes warrants the moderator gate. Anything
        # below the threshold (including minor-only edits) is adopted silently as
        # the new baseline, just like a rename or a colour tweak.
        if len(major) < self.major_change_threshold:
            if not changes:
                return BackupCycleResult(action="no_change")
            backup = await self.backups.create_backup(guild_id=guild_id, snapshot=snapshot)
            return BackupCycleResult(action="backed_up", backup=backup, changes=changes)

        # Enough major changes to require approval. Suppress a re-prompt of an identical change that
        # was just rejected, so Rob does not nag about a known/declined change.
        signature = self.change_signature(major)
        last_decided = await self.backups.get_last_decided(guild_id)
        if (
            last_decided is not None
            and last_decided.status == "rejected"
            and last_decided.change_signature == signature
        ):
            return BackupCycleResult(action="suppressed", changes=changes, major_changes=major)

        approval = await self.backups.create_approval(
            guild_id=guild_id,
            changes=major,
            change_signature=signature,
            pending_snapshot=snapshot,
            baseline_backup_id=baseline.id,
            required_approvals=self.required_approvals,
        )
        return BackupCycleResult(
            action="needs_approval",
            approval=approval,
            changes=changes,
            major_changes=major,
        )

    # -- approval decisions ---------------------------------------------------

    async def register_approval(self, *, approval_id: int, approver_user_id: int) -> ApprovalDecision:
        approval = await self.backups.get_approval(approval_id)
        if approval is None:
            return ApprovalDecision(status="gone", approval=None)
        if approval.status != "pending":
            return ApprovalDecision(status="gone", approval=approval)
        if approver_user_id in approval.approved_by:
            remaining = max(0, approval.required_approvals - len(approval.approved_by))
            return ApprovalDecision(status="duplicate", approval=approval, remaining=remaining)

        updated = await self.backups.add_approver(approval_id=approval_id, user_id=approver_user_id)
        if updated is None:
            return ApprovalDecision(status="gone", approval=None)
        # The approval may have been finalized between our read and this write;
        # add_approver returns the existing (non-pending) row in that race. Bail
        # out so we never promote a baseline for an already-decided approval.
        if updated.status != "pending":
            return ApprovalDecision(status="gone", approval=updated)

        if len(updated.approved_by) >= updated.required_approvals:
            # Claim the approval atomically *before* creating a baseline backup so
            # a concurrent decision can't produce a spurious extra backup row:
            # only the call whose finalize transition succeeds writes the backup.
            claimed = await self.backups.finalize(
                approval_id=approval_id,
                status="approved",
                decided_by_user_id=approver_user_id,
                decision_reason="Approved by required moderators.",
            )
            if claimed is None:
                return ApprovalDecision(status="gone", approval=updated)
            backup = await self.backups.create_backup(
                guild_id=claimed.guild_id,
                snapshot=claimed.pending_snapshot,
            )
            finalized = await self.backups.set_baseline(
                approval_id=approval_id,
                baseline_backup_id=backup.id,
            )
            return ApprovalDecision(status="approved", approval=finalized or claimed, remaining=0)

        remaining = max(0, updated.required_approvals - len(updated.approved_by))
        return ApprovalDecision(status="recorded", approval=updated, remaining=remaining)

    async def force_adopt(self, *, approval_id: int, reason: str) -> ServerBackupApproval | None:
        """Resolve a pending approval without the moderator gate.

        Safety net for when the approval prompt cannot be delivered (e.g. no
        backup approval channel is configured): rather than leave backups paused
        forever on a dangling pending approval, promote the pending snapshot to
        the baseline and mark the approval superseded.
        """

        approval = await self.backups.get_approval(approval_id)
        if approval is None or approval.status != "pending":
            return approval
        backup = await self.backups.create_backup(
            guild_id=approval.guild_id,
            snapshot=approval.pending_snapshot,
        )
        return await self.backups.finalize(
            approval_id=approval_id,
            status="superseded",
            decided_by_user_id=None,
            decision_reason=reason,
            baseline_backup_id=backup.id,
        )

    async def reject(self, *, approval_id: int, rejected_by_user_id: int) -> ApprovalDecision:
        approval = await self.backups.get_approval(approval_id)
        if approval is None or approval.status != "pending":
            return ApprovalDecision(status="gone", approval=approval)
        finalized = await self.backups.finalize(
            approval_id=approval_id,
            status="rejected",
            decided_by_user_id=rejected_by_user_id,
            decision_reason="Rejected by a moderator.",
        )
        return ApprovalDecision(status="rejected", approval=finalized or approval)
