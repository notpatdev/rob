"""Activity / inactive-role lifecycle.

A verified member who goes ``inactive_after_days`` without sending a message or
otherwise interacting loses the **Active** role, gains the **Inactive** role, and
is placed on the removal countdown: a first notice immediately, a final notice
``final_notice_days`` before removal, and a kick once ``remove_at`` passes.
Detecting that absence of activity is not event-driven, so a periodic sweep
(``process_guild``) flags newly-inactive members. The reverse — *coming back* —
is event-driven: ``register_member_activity`` restores the Active role and clears
the countdown the instant a member interacts again.

Members holding the **Unverified** role are parked as inactive (Inactive role on,
Active role off) but are never put on the kick countdown — they are new and have
simply not verified yet. Once the Unverified role is gone the normal rules apply.

The activity *signal* (``activity:{guild}:user:{uid}:last_active``) is written by
the activity tracker into ``bot_settings``; the per-member countdown lives in the
``inactive_users`` table.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import discord

from rob.config.guilds import is_protected_user
from rob.database.repositories.bot_state import BotStateRepository
from rob.database.repositories.guild_settings import GuildSettingsRepository
from rob.database.repositories.inactive_users import InactiveUsersRepository
from rob.services.maintenance_service import MaintenanceService
from rob.ui.cards.inactivity import (
    final_inactivity_warning_card,
    first_inactivity_warning_card,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class InactivitySnapshot:
    member: discord.Member
    remove_at: datetime


class InactivityService:
    def __init__(
        self,
        *,
        bot_state: BotStateRepository,
        guild_settings: GuildSettingsRepository,
        inactive_users: InactiveUsersRepository,
        enabled_default: bool,
        inactive_after_days: int,
        kick_grace_days: int,
        bootstrap_grace_days: int,
        final_notice_days: int,
        notice_channel_id: int | None,
        maintenance: MaintenanceService | None = None,
        afk_days: int = 7,
    ) -> None:
        self.bot_state = bot_state
        self.guild_settings = guild_settings
        self.inactive_users = inactive_users
        self.enabled_default = enabled_default
        self.inactive_after = timedelta(days=max(1, inactive_after_days))
        self.kick_grace = timedelta(days=max(1, kick_grace_days))
        self.bootstrap_grace = timedelta(days=max(1, bootstrap_grace_days))
        self.final_notice_window = timedelta(days=max(1, final_notice_days))
        self.notice_channel_id = notice_channel_id
        self.maintenance = maintenance
        self.afk_duration = timedelta(days=max(1, afk_days))

    # -- keys -----------------------------------------------------------------

    @staticmethod
    def activity_key(guild_id: int, member_id: int) -> str:
        return f"activity:{guild_id}:user:{member_id}:last_active"

    @staticmethod
    def afk_key(guild_id: int, member_id: int) -> str:
        return f"inactivity:{guild_id}:user:{member_id}:afk_until"

    def _enabled_key(self, guild_id: int) -> str:
        return f"inactivity:{guild_id}:enabled"

    def _bootstrapped_key(self, guild_id: int) -> str:
        return f"inactivity:{guild_id}:bootstrapped_at"

    # -- enable / activity ----------------------------------------------------

    async def is_enabled(self, guild_id: int) -> bool:
        value = await self.bot_state.get_text(self._enabled_key(guild_id))
        return self._parse_bool(value, default=self.enabled_default)

    async def set_enabled(self, guild_id: int, enabled: bool) -> None:
        await self.bot_state.set_value(self._enabled_key(guild_id), "true" if enabled else "false")

    async def record_activity(
        self,
        guild_id: int,
        member_id: int,
        *,
        when: datetime | None = None,
    ) -> None:
        moment = when or datetime.now(timezone.utc)
        await self.bot_state.set_value(self.activity_key(guild_id, member_id), moment.isoformat())

    async def get_last_activity(self, guild_id: int, member_id: int) -> datetime | None:
        return self._parse_optional_datetime(
            await self.bot_state.get_text(self.activity_key(guild_id, member_id))
        )

    async def set_member_afk(
        self, guild_id: int, member_id: int, *, now: datetime | None = None
    ) -> datetime:
        """Exempt a single member from the inactivity sweep for ``afk_duration``
        (a week). Backs the ``/afk`` command. Returns when the exemption lifts."""

        moment = now or datetime.now(timezone.utc)
        until = moment + self.afk_duration
        await self.bot_state.set_value(self.afk_key(guild_id, member_id), until.isoformat())
        return until

    async def get_afk_until(self, guild_id: int, member_id: int) -> datetime | None:
        return self._parse_optional_datetime(
            await self.bot_state.get_text(self.afk_key(guild_id, member_id))
        )

    @staticmethod
    def _can_read_history(channel: object, me: object | None) -> bool:
        if me is None:
            return True
        perms_for = getattr(channel, "permissions_for", None)
        if perms_for is None:
            return True
        try:
            perms = perms_for(me)
        except Exception:
            return False
        return bool(getattr(perms, "view_channel", True) and getattr(perms, "read_message_history", True))

    async def backfill_activity_from_history(
        self, guild: discord.Guild, *, days: int
    ) -> dict[str, int]:
        """Seed ``last_active`` from the last ``days`` of message history.

        Scans the readable text channels and active threads, records each
        non-bot author's most recent message time (only when newer than any
        existing record), and returns a summary. Used to bootstrap activity when
        the system is first enabled on a guild so already-active members are not
        wrongly flagged inactive before live tracking has any history.
        """

        cutoff = datetime.now(timezone.utc) - timedelta(days=max(1, days))
        me = getattr(guild, "me", None)
        latest: dict[int, datetime] = {}
        channels_scanned = 0

        sources: list[object] = list(getattr(guild, "text_channels", []))
        sources.extend(getattr(guild, "threads", []))
        for channel in sources:
            if not self._can_read_history(channel, me):
                continue
            history = getattr(channel, "history", None)
            if history is None:
                continue
            channels_scanned += 1
            try:
                async for message in history(after=cutoff, limit=None):
                    author = getattr(message, "author", None)
                    if author is None or getattr(author, "bot", False):
                        continue
                    ts = getattr(message, "created_at", None)
                    if ts is None:
                        continue
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if author.id not in latest or ts > latest[author.id]:
                        latest[author.id] = ts
            except (discord.Forbidden, discord.HTTPException):
                continue

        seeded = 0
        for user_id, ts in latest.items():
            existing = await self.get_last_activity(guild.id, user_id)
            if existing is None or ts > existing:
                await self.record_activity(guild.id, user_id, when=ts)
                seeded += 1

        log.info(
            "Backfilled activity for guild_id=%s: seeded %s of %s members across %s channels",
            guild.id,
            seeded,
            len(latest),
            channels_scanned,
        )
        return {"channels_scanned": channels_scanned, "users_seen": len(latest), "users_seeded": seeded}

    def counts_for_reactivation(
        self, channel_id: int | None, thread_parent_id: int | None = None
    ) -> bool:
        """Whether activity in this channel can earn an Inactive member their
        Active role back. When a main chat channel is configured
        (``notice_channel_id`` — the channel the inactivity notices point
        members to), only activity there (or in its threads) counts; with no
        channel configured, activity anywhere counts as before."""

        if self.notice_channel_id is None:
            return True
        return (
            channel_id == self.notice_channel_id
            or thread_parent_id == self.notice_channel_id
        )

    async def register_member_activity(
        self,
        guild: discord.Guild,
        member: discord.abc.User,
        *,
        channel_id: int | None = None,
        thread_parent_id: int | None = None,
    ) -> None:
        """Record activity for a member and reactivate them immediately if they
        were marked inactive.

        Called from the activity tracker on every message / reaction /
        interaction. The "going inactive" direction still needs the periodic
        sweep (absence of activity is not event-driven), but "coming back" is
        handled here in real time. The common case — an already-active member —
        only does the lightweight activity write plus an in-memory role check, so
        this stays cheap on every message.

        Members currently marked Inactive only earn activity credit — and their
        Active role back — in the main chat channel (see
        :meth:`counts_for_reactivation`); their activity elsewhere is ignored so
        the next sweep can't reactivate them either.
        """

        # Discord bots are never tracked by the inactivity system.
        if getattr(member, "bot", False):
            return

        roles_known = getattr(member, "roles", None) is not None
        enabled: bool | None = None
        settings = None
        if roles_known and self.notice_channel_id is not None:
            enabled = await self.is_enabled(guild.id)
            if enabled:
                settings = await self.guild_settings.get(guild.id)
                if (
                    settings is not None
                    and settings.inactive_role_id is not None
                    and self._has_role(member, settings.inactive_role_id)
                    and not is_protected_user(member.id)
                    and not self.counts_for_reactivation(channel_id, thread_parent_id)
                ):
                    log.debug(
                        "Ignoring activity outside the main channel for inactive "
                        "user_id=%s guild_id=%s channel_id=%s",
                        member.id,
                        guild.id,
                        channel_id,
                    )
                    return

        await self.record_activity(guild.id, member.id)
        # Skip non-guild authors (e.g. webhook/User objects have no roles).
        if not roles_known:
            return
        if enabled is None:
            enabled = await self.is_enabled(guild.id)
        if not enabled:
            return
        if settings is None:
            settings = await self.guild_settings.get(guild.id)
        if settings is None:
            return
        active_role = guild.get_role(settings.active_role_id) if settings.active_role_id else None
        inactive_role = guild.get_role(settings.inactive_role_id) if settings.inactive_role_id else None
        if active_role is None or inactive_role is None:
            return
        # Protected ("DO NOT TOUCH") accounts are always kept Active.
        if is_protected_user(member.id):
            await self._keep_active(guild.id, member, active_role, inactive_role, reason="Protected member")
            return
        # Unverified members stay parked as inactive regardless of activity.
        if settings.unverified_role_id is not None and self._has_role(member, settings.unverified_role_id):
            return
        # Only act when they are currently marked inactive (cheap in-memory check
        # — most messages are from already-active members and stop here).
        if not self._has_role(member, inactive_role.id):
            return
        await self._remove_role(member, inactive_role, reason="Member active again")
        await self._add_role(member, active_role, reason="Member active again")
        await self.inactive_users.clear(guild.id, member.id)
        log.info("Reactivated member user_id=%s guild_id=%s on activity", member.id, guild.id)

    async def sync_member_now(self, guild: discord.Guild, member: discord.abc.User) -> None:
        """Instantly set a member's Active/Inactive role to match their current
        status. Called on join and on verify/unverify role transitions so the
        roles always reflect reality immediately rather than at the next sweep:

        - Holds the Unverified role -> parked Inactive (Active off), no countdown.
        - Otherwise -> Active now (this fires on events that imply presence:
          joining, or verifying), activity stamped so the next sweep doesn't
          immediately re-flag them.

        Going *inactive* stays sweep-driven — there is no event for "stayed quiet
        for a week" — so this never newly flags a member inactive.
        """

        # Discord bots are never managed by the inactivity system.
        if getattr(member, "bot", False):
            return
        if getattr(member, "roles", None) is None:
            return
        if not await self.is_enabled(guild.id):
            return
        settings = await self.guild_settings.get(guild.id)
        if settings is None:
            return
        active_role = guild.get_role(settings.active_role_id) if settings.active_role_id else None
        inactive_role = guild.get_role(settings.inactive_role_id) if settings.inactive_role_id else None
        if active_role is None or inactive_role is None:
            return

        # Protected ("DO NOT TOUCH") accounts are always kept Active, even if
        # they somehow hold the unverified role.
        if is_protected_user(member.id):
            await self._keep_active(guild.id, member, active_role, inactive_role, reason="Protected member")
            return

        if settings.unverified_role_id is not None and self._has_role(member, settings.unverified_role_id):
            await self._remove_role(member, active_role, reason="Member is unverified")
            await self._add_role(member, inactive_role, reason="Member is unverified")
            await self.inactive_users.clear(guild.id, member.id)
            log.info("Parked unverified member user_id=%s guild_id=%s", member.id, guild.id)
            return

        await self.record_activity(guild.id, member.id)
        await self._remove_role(member, inactive_role, reason="Member verified/active")
        await self._add_role(member, active_role, reason="Member verified/active")
        await self.inactive_users.clear(guild.id, member.id)
        log.info("Activated member user_id=%s guild_id=%s instantly", member.id, guild.id)

    async def clear_member_state(self, guild_id: int, member_id: int) -> None:
        await self.inactive_users.clear(guild_id, member_id)

    async def list_inactive_members(
        self, guild: discord.Guild
    ) -> list[tuple[discord.Member, datetime | None]]:
        """Everyone currently holding the Inactive role, paired with their
        scheduled-removal time (``None`` when they are parked with no countdown —
        e.g. unverified or a manually-assigned role). Sorted soonest kick first,
        parked members last. Backs the ``/inactivelist`` command."""

        settings = await self.guild_settings.get(guild.id)
        inactive_role = (
            guild.get_role(settings.inactive_role_id)
            if settings is not None and settings.inactive_role_id is not None
            else None
        )
        if inactive_role is None:
            return []

        rows: list[tuple[discord.Member, datetime | None]] = []
        for member in getattr(inactive_role, "members", []):
            record = await self.inactive_users.get(guild.id, member.id)
            remove_at = record.remove_at if record is not None else None
            rows.append((member, remove_at))

        far_future = datetime.max.replace(tzinfo=timezone.utc)
        rows.sort(key=lambda item: (item[1] is None, item[1] or far_future, item[0].id))
        return rows

    # -- parsing helpers ------------------------------------------------------

    @staticmethod
    def _parse_bool(raw: str | None, *, default: bool = False) -> bool:
        if raw is None:
            return default
        lowered = raw.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
        return default

    @staticmethod
    def _parse_optional_datetime(raw: str | None) -> datetime | None:
        if raw is None:
            return None
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed

    @staticmethod
    def _is_eligible_member(member: discord.Member) -> bool:
        return not getattr(member, "bot", False)

    @staticmethod
    def _has_role(member: discord.Member, role_id: int | None) -> bool:
        if role_id is None:
            return False
        return any(role.id == role_id for role in getattr(member, "roles", []))

    def _notice_channel_hint(self) -> str:
        if self.notice_channel_id:
            return f"<#{self.notice_channel_id}>"
        return "the server chat"

    @staticmethod
    def _display_name(member: discord.Member) -> str:
        return (getattr(member, "nick", None) or member.display_name or member.name or "").strip() or member.name

    # -- notice builders (also used by /inactivitytest) -----------------------

    def _build_first_notice(self, member: discord.Member, remove_at: datetime, guild_name: str) -> dict[str, object]:
        return first_inactivity_warning_card(
            display_name=self._display_name(member),
            server_name=guild_name,
            remove_at_unix=int(remove_at.timestamp()),
            main_chat_channel=self._notice_channel_hint(),
        ).send_kwargs()

    def _build_final_notice(self, member: discord.Member, remove_at: datetime, guild_name: str) -> dict[str, object]:
        return final_inactivity_warning_card(
            display_name=self._display_name(member),
            server_name=guild_name,
            remove_at_unix=int(remove_at.timestamp()),
            main_chat_channel=self._notice_channel_hint(),
        ).send_kwargs()

    async def _send_dm(self, member: discord.Member, *, message_kwargs: dict[str, object], label: str) -> bool:
        try:
            await member.send(**message_kwargs)
            log.info("Sent inactivity %s DM to user_id=%s", label, member.id)
            return True
        except discord.Forbidden:
            log.info("Could not DM user_id=%s for inactivity %s (DMs closed).", member.id, label)
        except discord.HTTPException:
            log.warning("Failed to DM user_id=%s for inactivity %s", member.id, label, exc_info=True)
        return False

    # -- role plumbing --------------------------------------------------------

    async def _add_role(self, member: discord.Member, role: discord.Role | None, *, reason: str) -> None:
        if role is None or self._has_role(member, role.id):
            return
        try:
            await member.add_roles(role, reason=reason)
            log.info("Added role %s to user_id=%s guild_id=%s", role.id, member.id, member.guild.id)
        except discord.Forbidden:
            log.warning("Missing permission to add role %s to user_id=%s", role.id, member.id)
        except discord.HTTPException:
            log.warning("Failed to add role %s to user_id=%s", role.id, member.id, exc_info=True)

    async def _remove_role(self, member: discord.Member, role: discord.Role | None, *, reason: str) -> None:
        if role is None or not self._has_role(member, role.id):
            return
        try:
            await member.remove_roles(role, reason=reason)
            log.info("Removed role %s from user_id=%s guild_id=%s", role.id, member.id, member.guild.id)
        except discord.Forbidden:
            log.warning("Missing permission to remove role %s from user_id=%s", role.id, member.id)
        except discord.HTTPException:
            log.warning("Failed to remove role %s from user_id=%s", role.id, member.id, exc_info=True)

    async def _keep_active(
        self,
        guild_id: int,
        member: discord.Member,
        active_role: discord.Role,
        inactive_role: discord.Role,
        *,
        reason: str,
    ) -> None:
        """Force a member to the Active state and drop any countdown — used for
        active members and for protected ("DO NOT TOUCH") accounts."""

        await self._remove_role(member, inactive_role, reason=reason)
        await self._add_role(member, active_role, reason=reason)
        await self.inactive_users.clear(guild_id, member.id)

    # -- main sweep -----------------------------------------------------------

    async def process_guild(
        self,
        guild: discord.Guild,
        *,
        send_notifications: bool,
        perform_kicks: bool,
    ) -> list[InactivitySnapshot]:
        guild_id = guild.id
        if not await self.is_enabled(guild_id):
            return []
        if self.maintenance is not None and await self.maintenance.notifications_suppressed():
            send_notifications = False
            perform_kicks = False

        settings = await self.guild_settings.get(guild_id)
        if settings is None:
            return []
        active_role = guild.get_role(settings.active_role_id) if settings.active_role_id else None
        inactive_role = guild.get_role(settings.inactive_role_id) if settings.inactive_role_id else None
        unverified_role = guild.get_role(settings.unverified_role_id) if settings.unverified_role_id else None
        # Both the active and inactive roles must exist for the swap to mean anything.
        if active_role is None or inactive_role is None:
            return []

        now = datetime.now(timezone.utc)
        bootstrapped_at = self._parse_optional_datetime(
            await self.bot_state.get_text(self._bootstrapped_key(guild_id))
        )
        is_bootstrap_run = bootstrapped_at is None
        grace = self.bootstrap_grace if is_bootstrap_run else self.kick_grace

        # First sweep after enabling: seed activity from recent chat history so
        # members who were active before Rob started tracking are not wrongly
        # flagged inactive (we have no live history for them yet).
        if is_bootstrap_run:
            try:
                await self.backfill_activity_from_history(guild, days=self.inactive_after.days)
            except Exception:  # pragma: no cover - never let backfill abort the sweep
                log.exception("Activity backfill failed during bootstrap for guild_id=%s", guild_id)

        snapshots: list[InactivitySnapshot] = []
        for member in guild.members:
            if not self._is_eligible_member(member):
                continue

            # "DO NOT TOUCH" accounts (e.g. a deceased member's memorial account)
            # are kept Active and never marked inactive, DM'd, or kicked.
            if is_protected_user(member.id):
                await self._keep_active(guild_id, member, active_role, inactive_role, reason="Protected member")
                continue

            # Unverified members are parked as inactive, never on the kick clock.
            if unverified_role is not None and self._has_role(member, unverified_role.id):
                await self._remove_role(member, active_role, reason="Unverified member parked as inactive")
                await self._add_role(member, inactive_role, reason="Unverified member parked as inactive")
                await self.inactive_users.clear(guild_id, member.id)
                continue

            # Members who ran /afk are exempt from the sweep until their window
            # expires — kept Active, never flagged inactive, DM'd, or kicked.
            afk_until = await self.get_afk_until(guild_id, member.id)
            if afk_until is not None and now < afk_until:
                await self._keep_active(guild_id, member, active_role, inactive_role, reason="Member is AFK")
                continue

            last_activity = await self.get_last_activity(guild_id, member.id)
            effective_last = last_activity
            if effective_last is None:
                joined = getattr(member, "joined_at", None)
                effective_last = joined if joined is not None and joined.tzinfo is not None else now

            is_inactive = (now - effective_last) >= self.inactive_after

            if not is_inactive:
                # Active member: ensure Active on, Inactive off, countdown cleared.
                await self._remove_role(member, inactive_role, reason="Member is active again")
                await self._add_role(member, active_role, reason="Member is active")
                await self.inactive_users.clear(guild_id, member.id)
                continue

            # Inactive member: swap roles and ensure they are on the countdown.
            await self._remove_role(member, active_role, reason="Member inactive for a week")
            await self._add_role(member, inactive_role, reason="Member inactive for a week")

            record = await self.inactive_users.get(guild_id, member.id)
            freshly_created = False
            if record is None or record.remove_at is None:
                remove_at = now + grace
                record = await self.inactive_users.start_watching(
                    guild_id=guild_id,
                    discord_user_id=member.id,
                    inactive_role_assigned_at=now,
                    remove_at=remove_at,
                )
                freshly_created = True
            remove_at = record.remove_at or (now + grace)

            # Grandfather members the bootstrap run discovers: give them the
            # Inactive role + a full grace window, but skip the day-one first
            # notice (they still get the final notice before any kick). Members
            # who go inactive *after* bootstrap get the first notice immediately.
            initial_notice_sent = record.initial_notice_sent
            if freshly_created and is_bootstrap_run and not initial_notice_sent:
                await self.inactive_users.mark_initial_notice(guild_id, member.id)
                initial_notice_sent = True

            if send_notifications and not initial_notice_sent:
                await self._send_dm(
                    member,
                    message_kwargs=self._build_first_notice(member, remove_at, guild.name),
                    label="first-notice",
                )
                await self.inactive_users.mark_initial_notice(guild_id, member.id)

            if perform_kicks and now >= remove_at:
                try:
                    await member.kick(reason=f"Inactive member auto-removal (scheduled {remove_at.isoformat()})")
                    await self.inactive_users.clear(guild_id, member.id)
                    log.info("Kicked inactive member user_id=%s guild_id=%s", member.id, guild_id)
                except discord.Forbidden:
                    log.warning("Missing permission to kick inactive user_id=%s guild_id=%s", member.id, guild_id)
                except discord.HTTPException:
                    log.warning("Failed to kick inactive user_id=%s guild_id=%s", member.id, guild_id, exc_info=True)
                continue

            if (
                send_notifications
                and not record.final_notice_sent
                and now < remove_at
                and (remove_at - now) <= self.final_notice_window
            ):
                await self._send_dm(
                    member,
                    message_kwargs=self._build_final_notice(member, remove_at, guild.name),
                    label="final-notice",
                )
                await self.inactive_users.mark_final_notice(guild_id, member.id)

            snapshots.append(InactivitySnapshot(member=member, remove_at=remove_at))

        if is_bootstrap_run:
            await self.bot_state.set_value(self._bootstrapped_key(guild_id), now.isoformat())
        return snapshots
