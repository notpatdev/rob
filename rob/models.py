from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from rob.utils.send_ids import build_public_send_id


@dataclass(frozen=True)
class VibSettings:
    guild_id: int
    registration_channel_id: int | None
    leaderboard_channel_id: int | None
    send_track_channel_id: int | None
    counting_channel_id: int | None
    report_channel_id: int | None
    domme_role_id: int | None
    sub_role_id: int | None
    mod_role_id: int | None
    inactive_role_id: int | None
    warn_log_channel_id: int | None
    carlbot_user_id: int | None
    created_at: datetime
    updated_at: datetime
    # Role that gates leaderboard viewing in the test guild. Holding it grants
    # the /leaderboard command and (via channel perms set on the role) read
    # access to the #leaderboard channel. Inert outside the test guild.
    leaderboard_view_role_id: int | None = None
    # Activity / inactive-role system roles. ``active_role_id`` is held by
    # verified, recently-active members; ``unverified_role_id`` marks members who
    # have not yet passed server verification (parked as inactive, never kicked).
    active_role_id: int | None = None
    unverified_role_id: int | None = None
    # Trial-moderator role, pinged alongside ``mod_role_id`` on server-backup
    # major-change approval prompts and accepted as an approver of those prompts.
    trial_mod_role_id: int | None = None
    # Channel where the hourly server-backup system posts major-change approval
    # prompts (the 2-moderator gate). When unset, the backup system still takes
    # baseline snapshots but cannot gate major changes.
    backup_approval_channel_id: int | None = None


# Backward-compat alias during v2 transition.
GuildSettings = VibSettings


@dataclass(frozen=True)
class Domme:
    id: int
    bot_user_id: int | None
    guild_id: int
    discord_user_id: int
    throne_url: str | None
    throne_handle: str | None
    throne_creator_id: str | None
    tracking_status: str
    profile_status: str
    hide_own_purchases: bool | None
    webhook_secret: str | None
    webhook_secret_hash: str | None
    webhook_connected_at: datetime | None
    overlay_detected: bool
    last_overlay_check_at: datetime | None
    last_successful_event_at: datetime | None
    public_display_name: str | None
    public_display_name_updated_at: datetime | None
    registered_at: datetime
    created_at: datetime
    updated_at: datetime
    # Leaderboard + preference state (test-guild only behavior).
    leaderboard_visible: bool = True
    preferences_deferred_until: datetime | None = None
    preferences_confirmed_at: datetime | None = None


@dataclass(frozen=True)
class DommeOnboardingState:
    id: int
    guild_id: int
    discord_user_id: int
    stage: str
    pending_throne_input: str | None
    pending_throne_handle: str | None
    pending_throne_creator_id: str | None
    dm_channel_id: int | None
    dm_message_id: int | None
    last_interaction_at: datetime
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class Sub:
    id: int
    guild_id: int
    discord_user_id: int
    send_name: str
    registered_at: datetime
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class SubSendName:
    id: int
    guild_id: int
    sub_id: int
    discord_user_id: int
    send_name: str
    is_primary: bool
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ThroneCreator:
    id: int
    guild_id: int
    domme_id: int | None
    discord_user_id: int
    throne_handle: str
    throne_creator_id: str
    hide_own_purchases: bool | None
    tracking_mode: str
    webhook_secret: str | None
    webhook_secret_hash: str | None
    webhook_connected_at: datetime | None
    overlay_detected: bool
    last_overlay_check_at: datetime | None
    last_successful_event_at: datetime | None
    last_test_webhook_at: datetime | None
    setup_verified_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class SendRecord:
    id: int
    guild_id: int
    domme_id: int | None
    domme_user_id: int
    sub_id: int | None
    sub_user_id: int | None
    sub_name: str | None
    amount_cents: int
    currency: str
    method: str | None
    source: str
    item_name: str | None
    item_image_url: str | None
    external_id: str | None
    event_id: str | None
    fallback_event_hash: str | None
    is_private: bool
    seeded: bool
    sent_at: datetime
    received_at: datetime
    discord_post_status: str
    discord_posted_at: datetime | None
    discord_message_id: int | None
    discord_post_error: str | None
    created_at: datetime
    is_test_send: bool = False
    _public_send_id: str | None = None
    original_amount_cents: int | None = None
    original_currency: str | None = None

    @property
    def public_send_id(self) -> str:
        return self._public_send_id or build_public_send_id(self)

    @property
    def stored_public_send_id(self) -> str | None:
        return self._public_send_id


@dataclass(frozen=True)
class NewSend:
    guild_id: int
    domme_id: int | None
    domme_user_id: int
    sub_id: int | None
    sub_user_id: int | None
    sub_name: str | None
    amount_cents: int
    currency: str
    method: str | None
    source: str
    item_name: str | None
    item_image_url: str | None
    external_id: str | None
    event_id: str | None
    fallback_event_hash: str | None
    is_private: bool
    seeded: bool
    sent_at: datetime
    discord_post_status: str
    is_test_send: bool = False
    original_amount_cents: int | None = None
    original_currency: str | None = None


@dataclass(frozen=True)
class SendChangeRequest:
    id: int
    guild_id: int
    domme_user_id: int
    action: str
    status: str
    requested_by: str
    requested_sub_name: str | None
    amount_cents: int | None
    currency: str | None
    method: str | None
    note: str | None
    target_send_id: int | None
    decision_reason: str | None
    request_channel_id: int | None
    request_message_id: int | None
    approved_by_user_id: int | None
    approved_send_id: int | None
    created_at: datetime
    updated_at: datetime
    decided_at: datetime | None


@dataclass(frozen=True)
class LeaderboardMessageRef:
    id: int
    guild_id: int
    message_key: str
    leaderboard_type: str | None
    channel_id: int
    message_id: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class CountingState:
    guild_id: int
    channel_id: int | None
    current_number: int
    last_user_id: int | None
    is_enabled: bool
    pending_restore: bool
    updated_at: datetime


@dataclass(frozen=True)
class CountRecoveryWindow:
    id: int
    guild_id: int
    channel_id: int
    failed_user_id: int
    failed_user_role: str
    required_domme_user_id: int | None
    required_domme_id: int | None
    expected_number: int
    attempted_content: str | None
    started_at: datetime
    expires_at: datetime
    resolved_at: datetime | None
    resolution: str | None
    created_at: datetime


@dataclass(frozen=True)
class CountBlock:
    id: int
    guild_id: int
    discord_user_id: int
    reason: str
    blocked_until: datetime
    created_at: datetime


@dataclass(frozen=True)
class InactiveUser:
    id: int
    guild_id: int
    bot_user_id: int | None
    discord_user_id: int
    inactive_role_assigned_at: datetime | None
    remove_at: datetime | None
    initial_notice_sent: bool
    final_notice_sent: bool
    status: str
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class ServerBackup:
    id: int
    guild_id: int
    snapshot: dict
    is_baseline: bool
    created_at: datetime


@dataclass(frozen=True)
class ServerBackupApproval:
    id: int
    guild_id: int
    status: str
    changes: list
    change_signature: str | None
    pending_snapshot: dict
    baseline_backup_id: int | None
    required_approvals: int
    approved_by: list[int]
    channel_id: int | None
    message_id: int | None
    decided_by_user_id: int | None
    decision_reason: str | None
    created_at: datetime
    updated_at: datetime
    decided_at: datetime | None


@dataclass(frozen=True)
class MaintenanceState:
    enabled: bool
    reason: str | None
    updated_at: datetime | None


@dataclass(frozen=True)
class LeaderboardEntry:
    label: str
    user_id: int | None
    total_cents: int
    send_count: int


@dataclass(frozen=True)
class LeaderboardSummary:
    total_cents: int
    send_count: int
    domme_count: int
    sub_count: int
    unclaimed_send_count: int = 0
    unclaimed_total_cents: int = 0


@dataclass(frozen=True)
class PersonalStatsSummary:
    total_cents: int
    send_count: int


@dataclass(frozen=True)
class LatestTrackedSend:
    id: int
    domme_user_id: int
    sub_user_id: int | None
    sub_name: str | None
    amount_cents: int
    currency: str
    method: str | None
    source: str
    item_name: str | None
    item_image_url: str | None
    sent_at: datetime


@dataclass(frozen=True)
class LeaderboardDiagnostics:
    guild_id: int
    registered_dommes: int
    counted_sends: int
    excluded_sends: int
    excluded_not_posted: int
    excluded_private: int
    excluded_test_send: int
    excluded_domme_mismatch: int
    excluded_guild_mismatch: int
    domme_rows: list[LeaderboardEntry]
    unmatched_sends: list[tuple[int, int, int]]




@dataclass(frozen=True)
class PublicLeaderboard:
    id: int
    guild_id: int
    public_token: str
    title: str
    enabled: bool
    theme: str
    created_at: datetime
    updated_at: datetime
@dataclass(frozen=True)
class QueueStatus:
    pending: int
    queued_maintenance: int
    posted: int
    failed: int
    ignored: int
