# Activity / inactive-role system & hourly server backups

Two systems, live on the **main + test guilds** (gated on
`rob.config.guilds.is_new_system_guild`). Both stay **dormant** until their
roles/channels are configured *and* the system is switched on per guild — so
promoting the code does not change anything on a guild until you run
`rob inactivity on` / `rob backup on` for it. Activity tracking, however, starts
recording immediately on a promoted guild so history is ready before you enable
the inactivity sweep.

## 1. Configure roles & channels (via `rob scan`)

Both systems read their roles/channel from `vib_settings`. `rob guild scan`
(friendly: `rob scan`) discovers them by name and prints the commands to set
them; `rob auto-apply` applies the suggestions.

New scan fields:

| Field | Purpose |
| --- | --- |
| `active_role_id` | Held by verified, recently-active members. |
| `inactive_role_id` | Given when a member goes inactive. |
| `unverified_role_id` | Members who have not verified yet (parked as inactive). |
| `mod_role_id` | Pinged on / can approve backup changes. |
| `trial_mod_role_id` | Pinged on / can approve backup changes. |
| `backup_approval_channel_id` | Where backup approval prompts are posted. |

```bash
rob scan --guild <guild_id>            # review current + suggested mappings
rob auto-apply --guild <guild_id> roles
rob auto-apply --guild <guild_id> backup_approval_channel_id
# or set a field to a specific id (when scan doesn't match it by name):
rob set-role    --guild <guild_id> --field active_role_id --role-id <id>
rob set-channel --guild <guild_id> --field backup_approval_channel_id --channel-id <id>
```

## 2. Activity / inactive-role system

Everything that *can* be event-driven is instant — Rob does not wait for the
sweep to react to a state change:

- **Talking / interacting** (message, reaction, slash/button use): activity is
  stamped, and a member who was marked inactive is restored to **Active**
  immediately. When `INACTIVITY_NOTICE_CHANNEL_ID` is set (the main chat
  channel the notices point members to), Active status is tracked **from that
  channel only** (threads under it count): activity anywhere else in the server
  stamps nothing — it neither keeps a member Active nor brings an Inactive
  member back, and the bootstrap history backfill likewise scans only that
  channel. Leave the channel unset to count activity anywhere.
- **Going unverified** (gains `unverified_role_id`): parked **Inactive**
  immediately (Active off, no countdown).
- **Becoming verified** (loses `unverified_role_id`) and **joining**: set to the
  correct state immediately — **Active** (or parked Inactive if still unverified).

Only the *absence* of activity ("stayed quiet for a week") has no event, so a
periodic **sweep** flags newly-inactive members. It runs every
`INACTIVITY_LOOP_MINUTES` (**default weekly = 10080**) and once on each restart.
Each sweep:

- **Active** (interacted within `INACTIVITY_INACTIVE_AFTER_DAYS`, default 7):
  keeps the Active role, loses the Inactive role, clears any countdown.
- **Inactive** (no activity for a week): loses the Active role, gains the
  Inactive role, goes on the `inactive_users` countdown, and gets a first DM
  notice. A final notice goes out `INACTIVITY_FINAL_NOTICE_DAYS` (default 7)
  before removal, and the member is kicked once the countdown
  (`INACTIVITY_KICK_GRACE_DAYS`, default 14 → ~3 weeks total) expires. With the
  weekly cadence these stages land on consecutive weekly sweeps.
- **Unverified** (holds `unverified_role_id`): parked as inactive (Inactive role
  on, Active off) but **never** on the kick countdown. Verifying counts as
  activity and restores the Active role.

**Protected ("DO NOT TOUCH") accounts.** User ids in
`rob.config.guilds.PROTECTED_USER_IDS` are never marked inactive, DM'd, or
kicked — every sweep keeps them **Active** and clears any countdown. Seeded with
a deceased member's memorial account; add more ids to that set as needed.

**Bots are ignored.** Discord bot accounts are skipped entirely — never tracked,
flagged inactive, DM'd, or kicked, and never handed the Active/Inactive role when
they join.

**AFK exemption.** Any member can run **`/afk`** to exempt **only themselves**
from the inactivity system for a week. While the AFK window is open the sweep
keeps them **Active** and never flags or removes them; it lapses automatically
(no command to undo). The window is the service's `afk_duration` (7 days).

**History backfill.** Rob only has live activity for a guild from the moment the
system is promoted there, so the **first sweep after enabling auto-scans recent
chat history** (the last `INACTIVITY_INACTIVE_AFTER_DAYS` of messages across the
readable text channels + active threads) and seeds `last_active` from it — so
members who were active before Rob started tracking are not wrongly flagged
inactive. You can also run it on demand (it re-seeds, then runs a safe corrective
sweep that restores Active to now-active members **without** DMing or kicking):

```bash
rob inactivity backfill --guild <guild_id> [--days <n>]   # default 7 days
```

The first run after enabling uses `INACTIVITY_BOOTSTRAP_GRACE_DAYS` (default 21)
so nobody is kicked the moment the system turns on. Maintenance mode suppresses
DMs and kicks. Both `active_role_id` and `inactive_role_id` must be set or the
system no-ops.

```bash
rob inactivity status --guild <guild_id>
rob inactivity on  --guild <guild_id>
rob inactivity off --guild <guild_id>
rob inactivity backfill --guild <guild_id> [--days <n>]
```

Mod slash commands (main + test guilds): `/inactivelist` (lists **everyone holding
the Inactive role** with the time until each is kicked, soonest first; parked
members with no scheduled removal are shown last) and `/inactivitytest` (DMs you
the notice templates). Member command (anyone): **`/afk`** exempts the caller from
the inactivity system for a week.

## 3. Hourly server-backup system

Every `SERVER_BACKUP_LOOP_MINUTES` (default 60) Rob snapshots roles, channels,
and core server settings into `server_backups` and diffs against the last
baseline:

- **No changes** → nothing stored (the baseline still represents the guild).
- **Minor edits** (renames, reorders, colours, topics, slowmode) **or only a few
  major changes** (fewer than `SERVER_BACKUP_MAJOR_CHANGE_THRESHOLD`, default 5)
  → adopted silently as the new baseline. A single new channel, one permission
  tweak, or a lone role add no longer needs approval.
- **A batch of major changes** (`SERVER_BACKUP_MAJOR_CHANGE_THRESHOLD` or more at
  once — deleted/created roles or channels, changed role permissions, changed
  channel permission overwrites, or security-relevant server settings; i.e. a
  likely revamp) → backups pause and Rob posts a **`### Major Server Change
  Detected!`** prompt to `backup_approval_channel_id`, pinging the mod + trial-mod
  roles. Backups resume only after `SERVER_BACKUP_REQUIRED_APPROVALS` (default 2)
  **distinct** moderators approve, at which point the pending snapshot becomes the
  new baseline. A rejected change keeps the old baseline and is not re-prompted
  until the configuration changes again.

> The prompt carries the warning: **DO NOT ACCEPT THIS IF YOU ARE DOING A REVAMP
> UNLESS YOU ARE SURE EVERYTHING CURRENTLY WORKS.** Approving blesses the current
> structure as the restore point.

If no approval channel is configured, Rob cannot gate a change, so it logs a
warning and adopts the snapshot rather than pausing backups forever.

```bash
rob backup status --guild <guild_id>
rob backup on  --guild <guild_id>
rob backup off --guild <guild_id>
rob backup run --guild <guild_id>   # run one cycle now (via the running bot)
```

## Database

Run the build scripts (`scripts/db/build/012_inactivity_backup_settings.sql`,
`013_server_backups.sql`) and then the relevant grants file. See
`scripts/db/build/README.md`.
