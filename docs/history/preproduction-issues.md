# Pre-Production Issues & Required Fixes

## Executive Summary

Three critical issues block the pre-prod checklist from passing:

1. **Achievement card notifications don't post** (except `!secret`)
2. **Maintenance mode doesn't pause send tracker or leaderboard** updates
3. **Inactivity list command doesn't correctly identify inactive users**

All three must be fixed before proceeding to repo rename and production prep.

---

## Issue 1: Achievement Notifications Not Posting (When Maintenance is OFF)

### Current Behavior
- Achievements unlock in the database
- **No card notification appears in Discord** (except for `!secret` which works)
- Only `!secret` achievement works because it uses a direct DM path that bypasses the callback system

### Root Cause
**Bug in `rob/services/send_queue_service.py` lines 300-313 and 368-421:**

The `_announce_callback()` helper returns `None` when `announce_channel is None`:

```python
def _announce_callback(user_id: int):
    if announce_channel is None:
        return None  # BUG: Returns None instead of a no-op callback

    async def _callback(achievement) -> None:
        await announce_channel.send(...)

    return _callback
```

Then this `None` is passed as `on_unlocked` to `unlock_achievement()`, which means the callback never runs.

Additionally, **many achievement unlocks don't pass the callback at all**. Lines 368-421 call `_unlock()` without `on_unlocked`:

```python
if stats.total_cents >= 10_000:
    await _unlock(
        user_id=domme_user_id,
        achievement_key="domme_100_tracked",
        source="send:posted",
    )  # Missing on_unlocked parameter
```

### Required Fix

**File: `rob/services/send_queue_service.py`**

Replace the `_unlock_send_achievements` method (lines 280-490):

```python
async def _unlock_send_achievements(
    self,
    send,
    *,
    previous_leader_user_id: int | None,
    announce_channel: discord.TextChannel | None,
) -> None:
    if self.achievements is None:
        return

    guild_id = send.guild_id
    domme_user_id = send.domme_user_id
    guild = self.bot.get_guild(guild_id)

    def _unlock_display_name(user_id: int) -> str:
        member = guild.get_member(user_id) if guild is not None else None
        if member is not None:
            return member.display_name
        return f"<@{user_id}>"

    def _announce_callback(user_id: int):
        async def _callback(achievement) -> None:
            # CHECK INSIDE CALLBACK, NOT BEFORE RETURNING
            if announce_channel is None:
                return
            await announce_channel.send(
                **achievement_unlocked_card(
                    achievement,
                    unlocked_by_display_name=_unlock_display_name(user_id),
                    unlocked_by_user_id=user_id,
                ).send_kwargs()
            )

        # ALWAYS RETURN A FUNCTION, NEVER None
        return _callback

    async def _unlock(
        *,
        user_id: int,
        achievement_key: str,
        source: str,
        metadata: dict | None = None,
    ) -> bool:
        return await self.achievements.unlock_achievement(
            guild_id=guild_id,
            discord_user_id=user_id,
            achievement_key=achievement_key,
            source=source,
            metadata=metadata,
            on_unlocked=_announce_callback(user_id),  # ALWAYS pass callback
        )

    if send.is_test_send:
        await _unlock(
            user_id=domme_user_id,
            achievement_key="domme_first_test_send",
            source="send:test",
        )
    else:
        await _unlock(
            user_id=domme_user_id,
            achievement_key="domme_first_tracked_send",
            source="send:posted",
        )

    if send.source.startswith("manual:"):
        await _unlock(
            user_id=domme_user_id,
            achievement_key="domme_manual_send",
            source="send:manual",
        )

    if send.source == "throne_webhook" and not send.is_test_send:
        await _unlock(
            user_id=domme_user_id,
            achievement_key="throne_first_real_auto_send",
            source="send:throne",
        )

    if self.leaderboards is None:
        return

    stats = await self.leaderboards.get_domme_stats(
        guild_id,
        domme_user_id=domme_user_id,
        include_test_sends=self.include_test_sends,
        test_gifter_usernames=self.test_gifter_usernames,
        owner_test_user_id=self.owner_test_user_id,
    )
    if not send.is_test_send:
        if stats.total_cents >= 10_000:
            await _unlock(
                user_id=domme_user_id,
                achievement_key="domme_100_tracked",
                source="send:posted",
            )
        if stats.total_cents >= 100_000:
            await _unlock(
                user_id=domme_user_id,
                achievement_key="domme_1000_tracked",
                source="send:posted",
            )
        if stats.total_cents >= 500_000:
            await _unlock(
                user_id=domme_user_id,
                achievement_key="domme_5000_tracked",
                source="send:posted",
            )

    for threshold, key in ((10, "domme_10_sends_received"), (50, "domme_50_sends_received"), (100, "domme_100_sends_received")):
        if stats.send_count >= threshold:
            await _unlock(
                user_id=domme_user_id,
                achievement_key=key,
                source="send:posted",
            )

    rank = await self.leaderboards.get_domme_rank(
        guild_id,
        domme_user_id=domme_user_id,
        include_test_sends=self.include_test_sends,
        test_gifter_usernames=self.test_gifter_usernames,
        owner_test_user_id=self.owner_test_user_id,
    )
    if rank is not None and rank <= 10:
        await _unlock(
            user_id=domme_user_id,
            achievement_key="domme_top_10",
            source="leaderboard:rank",
            metadata={"rank": rank},
        )
    if rank == 1:
        already_unlocked = await self.achievements.get_user_achievement_keys(
            guild_id=guild_id,
            discord_user_id=domme_user_id,
        )
        await self.achievements.unlock_achievement(
            guild_id=guild_id,
            discord_user_id=domme_user_id,
            achievement_key="domme_first_place",
            source="leaderboard:rank",
            on_unlocked=_announce_callback(domme_user_id),
        )
        if (
            previous_leader_user_id is not None
            and previous_leader_user_id != domme_user_id
            and "domme_first_place" in already_unlocked
        ):
            await _unlock(
                user_id=domme_user_id,
                achievement_key="domme_regain_first",
                source="leaderboard:rank",
            )

    sub_user_id = send.sub_user_id
    if sub_user_id is None:
        return

    await _unlock(
        user_id=sub_user_id,
        achievement_key="sub_first_send",
        source="send:posted",
    )

    sub_stats = await self.leaderboards.get_sub_stats(
        guild_id,
        sub_user_id=sub_user_id,
        include_test_sends=self.include_test_sends,
        test_gifter_usernames=self.test_gifter_usernames,
        owner_test_user_id=self.owner_test_user_id,
    )
    if sub_stats.total_cents >= 10_000:
        await _unlock(
            user_id=sub_user_id,
            achievement_key="sub_100_sent",
            source="send:posted",
        )
    if sub_stats.total_cents >= 100_000:
        await _unlock(
            user_id=sub_user_id,
            achievement_key="sub_1000_sent",
            source="send:posted",
        )
    if sub_stats.total_cents >= 500_000:
        await _unlock(
            user_id=sub_user_id,
            achievement_key="sub_5000_sent",
            source="send:posted",
        )

    for threshold, key in ((10, "sub_10_sends"), (50, "sub_50_sends"), (100, "sub_100_sends")):
        if sub_stats.send_count >= threshold:
            await _unlock(
                user_id=sub_user_id,
                achievement_key=key,
                source="send:posted",
            )

    current_leader = await self.leaderboard_service.get_current_leader(guild_id)
    if (
        current_leader is not None
        and current_leader.user_id == domme_user_id
        and previous_leader_user_id is not None
        and previous_leader_user_id != domme_user_id
    ):
        await _unlock(
            user_id=sub_user_id,
            achievement_key="sub_kingmaker",
            source="leaderboard:leader_change",
            metadata={"new_leader_user_id": domme_user_id},
        )
```

### Key Changes
1. **`_announce_callback()` now always returns a function** — never `None`
2. **Channel check moved inside the async callback** — if channel is unavailable, the callback silently no-ops instead of preventing execution
3. **All `_unlock()` calls now use the shared callback** — ensures all achievements are announced, not just the first few

### Testing
After applying this fix:
1. Post a send in the test guild
2. Verify achievement card appears in the send-tracker channel
3. Run `/achievements` to verify new achievements post in that context too
4. Verify counting achievements post immediately when count succeeds

---

## Issue 2: Maintenance Mode Doesn't Pause Send Tracker or Leaderboard

### Current Behavior
- Maintenance mode is enabled with `rob maintenance on "reason"`
- **Sends still post to send-tracker channel** during maintenance
- **Leaderboard updates every time a send arrives** during maintenance
- Only the **stats card correctly shows "🟠 Paused | Under Maintenance"**

**Expected behavior:** During maintenance, sends should queue and leaderboard should not update.

### Root Cause
**Bug in `rob/services/send_queue_service.py` lines 106-143:**

The `process_cycle()` method only checks maintenance to release queued sends, but never prevents new posts:

```python
async def process_cycle(self) -> None:
    if not await self.maintenance.is_enabled():
        released = await self.sends.release_queued_maintenance()
        # Only releases IF NOT in maintenance; doesn't prevent posting
    
    # ... continues to process and POST sends regardless
    for send in pending:
        ok = await self._post_send(send)  # Posts even during maintenance!
        if ok:
            await self.leaderboard_service.refresh_guild(send.guild_id)  # Refreshes during maintenance!
```

And in `_post_send()` (lines 215-278), there's **no maintenance check before posting**.

### Required Fix

**File: `rob/services/send_queue_service.py`**

Replace `process_cycle()` method (lines 106-143) and `_post_send()` method (lines 215-278):

```python
async def process_cycle(self) -> None:
    # Release queued maintenance sends when maintenance ends
    if not await self.maintenance.is_enabled():
        released = await self.sends.release_queued_maintenance()
        if released:
            log.info("Released %s queued maintenance send(s).", released)

    log.info("Send queue cycle started.")
    pending = await self.sends.fetch_for_status("pending", limit=50)
    log.info("Pending sends found: %s", len(pending))
    if self.counting_service is not None:
        queued_maintenance = await self.sends.fetch_for_status("queued_maintenance", limit=50)
        recovery_candidates = list(pending) + list(queued_maintenance)
        for send in recovery_candidates:
            try:
                await self.counting_service.process_send_for_count_rescue(send)
            except Exception:
                log.exception(
                    "Count rescue evaluation failed for send_id=%s guild_id=%s.",
                    send.id,
                    send.guild_id,
                )

    # ONLY POST SENDS IF MAINTENANCE IS NOT ENABLED
    if not await self.maintenance.is_enabled():
        for send in pending:
            ok = await self._post_send(send)
            if ok:
                log.info("Posted send id=%s guild_id=%s", send.id, send.guild_id)
                try:
                    log.info("Refreshing leaderboard for guild_id=%s", send.guild_id)
                    await self.leaderboard_service.refresh_guild(send.guild_id)
                except Exception:
                    log.exception(
                        "Leaderboard refresh failed after posted send_id=%s guild_id=%s.",
                        send.id,
                        send.guild_id,
                    )

    # ONLY REFRESH LEADERBOARD IF MAINTENANCE IS NOT ENABLED
    if not await self.maintenance.is_enabled():
        if await self.maintenance.consume_leaderboard_refresh_request():
            await self.leaderboard_service.refresh_all_guilds()

async def process_idle_tasks(self) -> None:
    """Run slow maintenance work without sweeping pending sends every tick."""
    if not await self.maintenance.is_enabled():
        released = await self.sends.release_queued_maintenance()
        if released:
            log.info("Released %s queued maintenance send(s).", released)
            await self.process_cycle()

    # ONLY REFRESH IF NOT IN MAINTENANCE
    if not await self.maintenance.is_enabled():
        if await self.maintenance.consume_leaderboard_refresh_request():
            await self.leaderboard_service.refresh_all_guilds()

async def _post_send(self, send) -> bool:
    # PREVENT POSTING DURING MAINTENANCE
    if await self.maintenance.is_enabled():
        log.info(
            "Send id=%s guild_id=%s held during maintenance window.",
            send.id,
            send.guild_id,
        )
        # Re-queue as queued_maintenance
        await self.sends.update_status(send.id, "queued_maintenance")
        return False

    settings = await self.guild_settings.get(send.guild_id)
    if settings is None or settings.send_track_channel_id is None:
        await self.sends.mark_failed(send.id, error="Missing send tracking channel configuration.")
        return False

    guild = self.bot.get_guild(send.guild_id)
    if guild is None:
        await self.sends.mark_failed(send.id, error="Guild not available to bot.")
        return False

    channel = guild.get_channel(settings.send_track_channel_id)
    if channel is None:
        try:
            channel = await guild.fetch_channel(settings.send_track_channel_id)
        except (discord.NotFound, discord.HTTPException) as exc:
            await self.sends.mark_failed(send.id, error=f"Send tracking channel unavailable: {exc}")
            return False

    if not isinstance(channel, discord.TextChannel):
        await self.sends.mark_failed(send.id, error="Configured send tracking channel is not a text channel.")
        return False

    previous_leader = await self.leaderboard_service.get_current_leader(send.guild_id)
    try:
        msg = send_card(
            send=send,
            domme_label=f"<@{send.domme_user_id}>",
            sub_display=build_sub_display(
                send,
                test_gifter_usernames=self.test_gifter_usernames,
            ),
        )
        message = await channel.send(**msg.send_kwargs())
    except discord.HTTPException as exc:
        await self.sends.mark_failed(send.id, error=f"Discord post failed: {exc}")
        return False

    await self.sends.mark_posted(send.id, message_id=message.id)
    log.info("Marked send posted id=%s message_id=%s", send.id, message.id)
    try:
        await self._unlock_send_achievements(
            send,
            previous_leader_user_id=previous_leader.user_id if previous_leader is not None else None,
            announce_channel=channel,
        )
    except Exception:
        log.exception(
            "Achievement unlock evaluation failed for send_id=%s guild_id=%s.",
            send.id,
            send.guild_id,
        )
    try:
        await self.leaderboard_service.maybe_post_leader_alert(
            send.guild_id,
            previous_leader_user_id=previous_leader.user_id if previous_leader is not None else None,
        )
    except Exception:
        log.exception(
            "Leader alert failed for send_id=%s guild_id=%s after successful send post.",
            send.id,
            send.guild_id,
        )
    return True
```

### Required Database Method

You need to add an `update_status()` method to `SendsRepository` if it doesn't exist.

**File: `rob/database/repositories/sends.py`** (add this method to the class):

```python
async def update_status(self, send_id: int, status: str) -> bool:
    """Update the discord_post_status of a send."""
    if status not in self.VALID_STATUSES:
        raise ValueError(f"Invalid status: {status}. Must be one of {self.VALID_STATUSES}")
    
    async with self.database.acquire() as connection:
        row = await connection.fetchrow(
            """
            UPDATE sends
            SET discord_post_status = $1
            WHERE id = $2
            RETURNING id
            """,
            status,
            send_id,
        )
    return row is not None
```

### Key Changes
1. **`_post_send()` checks maintenance first** — if enabled, re-queues send as `queued_maintenance` and returns False
2. **`process_cycle()` skips posting loop during maintenance** — only releases queued sends
3. **`process_cycle()` skips leaderboard refresh during maintenance** — leaderboard only updates after maintenance ends
4. **`process_idle_tasks()` also respects maintenance** — consistent behavior

### Testing
After applying this fix:
1. Run: `rob maintenance on "Pre-prod rehearsal"`
2. Post a send to Throne or use `rob send add <handle> 100`
3. Verify: Send appears in DB as `queued_maintenance`, NOT in Discord
4. Verify: Leaderboard does NOT update while maintenance is on
5. Run: `rob maintenance off`
6. Verify: Queued sends post immediately, leaderboard updates

---

## Issue 3: Inactivity List Command Doesn't Identify Inactive Users

### Current Behavior
- The `/inactivelist` command only scans users who **already have the inactive role**
- It does **not scan all members** to identify who should be marked inactive
- Users must be manually assigned the inactive role first

**Expected behavior:** The command should scan all members, identify those inactive for X days, and show them for potential removal.

### Root Cause
**Architecture issue in `rob/services/inactivity_service.py` lines 164-231:**

The `process_guild()` method assumes users are **already marked with the inactive role**. It doesn't discover inactive users; it only processes those who have the role.

There is **no activity tracking system** in place — no way to know when a user last posted a message.

### Required Fix

This requires two changes:

#### Part A: Add Activity Tracking

**File: `rob/discord/cogs/activity_tracker.py`** (create new file):

```python
from __future__ import annotations

import logging

import discord
from discord.ext import commands

log = logging.getLogger(__name__)


class ActivityTrackerCog(commands.Cog):
    """Tracks member message activity to support inactivity detection."""

    def __init__(self, bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Update last activity timestamp for non-bot users."""
        if message.author.bot:
            return
        if message.guild is None:
            return

        try:
            bot_state = getattr(self.bot, "bot_state_repo", None)
            if bot_state is None:
                return

            guild_id = message.guild.id
            user_id = message.author.id
            
            # Store timestamp in bot_state with guild+user+timestamp format
            key = f"activity:{guild_id}:user:{user_id}:last_active"
            now_iso = discord.utils.utcnow().isoformat()
            await bot_state.set_value(key, now_iso)
            
        except Exception:
            log.exception(
                "Failed to record activity for user_id=%s guild_id=%s",
                message.author.id,
                message.guild.id,
            )


async def setup(bot) -> None:
    await bot.add_cog(ActivityTrackerCog(bot))
```

Then **register this cog in your bot startup** (in `rob/discord/client.py` or equivalent):

```python
await bot.load_extension("rob.discord.cogs.activity_tracker")
```

#### Part B: Update Inactivity Service to Scan All Members

**File: `rob/services/inactivity_service.py`** - Replace the `process_guild()` method (lines 164-231):

```python
async def process_guild(self, guild: discord.Guild, *, send_notifications: bool, perform_kicks: bool) -> list[InactivitySnapshot]:
    guild_id = guild.id
    if not await self.is_enabled(guild_id):
        return []
    if self.maintenance is not None and await self.maintenance.notifications_suppressed():
        send_notifications = False
        perform_kicks = False

    settings = await self.guild_settings.get(guild_id)
    if settings is None or settings.inactive_role_id is None:
        return []
    
    inactive_role = guild.get_role(settings.inactive_role_id)
    if inactive_role is None:
        return []

    # SCAN ALL ELIGIBLE MEMBERS, NOT JUST THOSE WITH THE ROLE
    all_members = [member for member in guild.members if self._is_eligible_member(member)]
    if not all_members:
        return []

    now = datetime.now(timezone.utc)
    bootstrapped_at = self._parse_optional_datetime(await self.bot_state.get_text(self._bootstrapped_key(guild_id)))
    is_bootstrap_run = bootstrapped_at is None
    snapshots: list[InactivitySnapshot] = []

    for member in all_members:
        state = await self._load_member_state(guild_id, member.id)
        assigned_at = state["assigned_at"] if isinstance(state["assigned_at"], datetime) else None
        remove_at = state["remove_at"] if isinstance(state["remove_at"], datetime) else None
        initial_notice_sent = bool(state["initial_notice_sent"])
        final_notice_sent = bool(state["final_notice_sent"])

        # GET LAST ACTIVITY TIMESTAMP
        last_activity_iso = await self.bot_state.get_text(
            f"activity:{guild_id}:user:{member.id}:last_active"
        )
        last_activity = self._parse_optional_datetime(last_activity_iso)

        # DETERMINE IF MEMBER SHOULD BE TRACKED
        if assigned_at is None or remove_at is None:
            # New tracking for this member
            if last_activity is not None:
                # Has activity history; use it as reference point
                assigned_at = last_activity
            elif member.joined_at is not None and member.joined_at.tzinfo is not None:
                # Use join time if no activity
                assigned_at = member.joined_at
            else:
                # Fallback to now
                assigned_at = now
            
            grace = self.bootstrap_grace if is_bootstrap_run else self.assignment_grace
            remove_at = assigned_at + grace
            initial_notice_sent = False
            final_notice_sent = False
            await self._save_member_state(
                guild_id,
                member.id,
                assigned_at=assigned_at,
                remove_at=remove_at,
                initial_notice_sent=False,
                final_notice_sent=False,
            )
        else:
            # EXISTING TRACKING: RESET TIMERS IF MEMBER HAS BEEN ACTIVE
            if last_activity is not None and last_activity > assigned_at:
                # Member was active recently; clear inactivity tracking
                await self.clear_member_state(guild_id, member.id)
                # Remove inactive role if present
                if inactive_role in member.roles:
                    try:
                        await member.remove_roles(inactive_role)
                        log.info("Removed inactive role from active member user_id=%s guild_id=%s", member.id, guild_id)
                    except discord.Forbidden:
                        log.warning("Could not remove inactive role from user_id=%s (permission denied)", member.id)
                    except discord.HTTPException:
                        log.warning("Failed to remove inactive role from user_id=%s", member.id, exc_info=True)
                continue

        # ASSIGN INACTIVE ROLE IF NEEDED
        if now >= remove_at and inactive_role not in member.roles:
            try:
                await member.add_roles(inactive_role)
                log.info("Added inactive role to user_id=%s guild_id=%s", member.id, guild_id)
            except discord.Forbidden:
                log.warning("Could not assign inactive role to user_id=%s (permission denied)", member.id)
            except discord.HTTPException:
                log.warning("Failed to assign inactive role to user_id=%s", member.id, exc_info=True)

        first_warning_due_at = self._first_warning_due_at(assigned_at, remove_at)
        if send_notifications and not initial_notice_sent and now >= first_warning_due_at:
            await self._send_dm(
                member,
                message_kwargs=self._build_first_notice(member, remove_at, guild.name),
                label="warning-notice",
            )
            await self._save_member_state(guild_id, member.id, initial_notice_sent=True)

        if perform_kicks and now >= remove_at:
            try:
                await member.kick(reason=f"Inactive member auto-removal scheduled at {remove_at.isoformat()}")
                await self.clear_member_state(guild_id, member.id)
                log.info("Kicked inactive member user_id=%s guild_id=%s", member.id, guild_id)
            except discord.Forbidden:
                log.warning("Missing permission to kick inactive member user_id=%s guild_id=%s", member.id, guild_id)
            except discord.HTTPException:
                log.warning("Failed to kick inactive member user_id=%s guild_id=%s", member.id, guild_id, exc_info=True)
            continue

        if send_notifications and not final_notice_sent and now < remove_at and (remove_at - now) <= self.final_notice_window:
            await self._send_dm(
                member,
                message_kwargs=self._build_final_notice(member, remove_at, guild.name),
                label="final-notice",
            )
            await self._save_member_state(guild_id, member.id, final_notice_sent=True)

        snapshots.append(InactivitySnapshot(member=member, remove_at=remove_at))

    if is_bootstrap_run:
        await self.bot_state.set_value(self._bootstrapped_key(guild_id), now.isoformat())
    
    return snapshots
```

### Key Changes

**Activity Tracker:**
1. New cog that listens to `on_message` events
2. Records last activity timestamp for each user per guild in `bot_state`
3. Timestamps stored as ISO format strings

**Inactivity Service:**
1. **Scans ALL members**, not just those with the inactive role
2. **Checks last activity from `bot_state`** when determining inactivity
3. **Automatically removes inactive role** if member becomes active again (has posted recently)
4. **Automatically adds inactive role** when member hits the removal date
5. **Clears tracking state** when member posts a message again

### Testing
After applying this fix:
1. Enable inactivity: `scripts/ops.py inactivity on --guild-id 1506597978251591813`
2. Wait 1+ minute for the loop to run
3. Check logs: `rob logs bot | grep inactivity`
4. Verify: Members scanned include ALL members, not just those with role
5. Post a message as a tracked member; verify role is removed within 60 seconds
6. Don't post for the grace period; verify you get warning DMs and role is assigned

---

## Testing Checklist

Run through these tests in order after all three fixes are applied:

### Test 1: Achievement Notifications
```bash
rob send add <domme_handle> 10 --guild 1506597978251591813
# Approve in Discord
# Verify: Achievement card posts immediately in send-tracker channel
# Verify: /achievements shows new unlocks
# Verify: Count achievements post when count succeeds
```

### Test 2: Maintenance Mode
```bash
rob maintenance on "Testing pause"
rob send add <domme_handle> 10 --guild 1506597978251591813
# Approve in Discord
# Verify: Send does NOT post (DB shows queued_maintenance)
# Verify: Leaderboard does NOT update
# Verify: Leaderboard shows "🟠 Paused | Under Maintenance"

rob maintenance off
# Verify: Queued send posts immediately
# Verify: Leaderboard updates
```

### Test 3: Inactivity Detection
```bash
# Enable inactivity
scripts/ops.py inactivity on --guild-id 1506597978251591813

# Wait ~60 seconds for loop
# Verify: Log shows all members scanned
# Post a message as a test user
# Wait ~30 seconds
# Verify: Inactive role removed from you

# Don't post for grace period
# Verify: First warning DM arrives
# Verify: Second warning DM arrives after another period
# Verify: Kicked when remove_at is reached
```

---

## Summary

| Issue | File | Change | Impact |
|-------|------|--------|--------|
| Achievements | `send_queue_service.py` | Always return callback function; check channel inside callback | Fixes all achievement notifications |
| Maintenance | `send_queue_service.py` + `sends.py` | Check maintenance before posting; re-queue as queued_maintenance | Pauses sends and leaderboard updates during maintenance |
| Inactivity | `inactivity_service.py` + new `activity_tracker.py` | Scan all members; track message activity | Correctly identifies and removes inactive users |

All three fixes are required for pre-production readiness.
