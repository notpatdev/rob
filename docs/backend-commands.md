# Backend Commands

Use `ops/cli/rob` (or install the global wrapper) for operations.

## Install global command

```bash
ops/install-rob-global.sh
```

## Core command groups

```bash
rob status
rob logs bot
rob logs webhook
rob restart bot
rob restart webhook

rob maintenance status
rob maintenance on "reason"
rob maintenance off

rob queue status
rob queue flush

rob leaderboard refresh
rob leaderboard status --guild-id <guild_id>
rob leaderboard preview --guild-id <guild_id>
rob leaderboard diagnose --guild-id <guild_id>
rob leaderboard repair-send-dommes --guild-id <guild_id> --dry-run
rob leaderboard repair-send-dommes --guild-id <guild_id>

rob throne status --guild-id <guild_id>
rob throne dommes --guild-id <guild_id>
rob throne subs --guild-id <guild_id>
rob throne invalidate-test-sends

rob sends list --status all --guild-id <guild_id> --limit 25
rob sends backfill-public-ids
rob sends repair-mentions --dry-run
rob sends repair-mentions
rob sends mark-posted <send_id>

rob guild scan --guild-id <guild_id>
rob guild set-channel --guild-id <guild_id> --field leaderboard_channel_id --channel-id <channel_id>
rob guild set-role --guild-id <guild_id> --field domme_role_id --role-id <role_id>

# `rob guild scan` (friendly: `rob scan` / `rob auto-apply`) also discovers and
# can set the activity + backup fields:
#   roles:    active_role_id, inactive_role_id, unverified_role_id,
#             mod_role_id, trial_mod_role_id
#   channels: backup_approval_channel_id

rob count status
rob count set <number>

rob inactivity status --guild-id <guild_id>
rob inactivity on --guild-id <guild_id>
rob inactivity off --guild-id <guild_id>

rob backup status --guild-id <guild_id>
rob backup on --guild-id <guild_id>
rob backup off --guild-id <guild_id>
rob backup run --guild-id <guild_id>

rob migration audit --guild <guild_id>
rob webhook preview --guild <guild_id>
rob webhook send --guild <guild_id> --all --limit 10
rob webhook send --guild <guild_id> --discord-user-id <discord_user_id>

rob clear rob_dev_v2
```

## Migration rehearsal commands

```bash
rob migration audit --guild <guild_id>
rob webhook preview --guild <guild_id>
rob webhook send --guild <guild_id> --all
rob webhook send --guild <guild_id> --all --limit 10
rob webhook send --guild <guild_id> --discord-user-id <discord_user_id>
rob clear rob_dev_v2
```

- `rob migration audit` prints a guild-level rehearsal summary for imported Dom/mes, Subs, sends, count state, leaderboard refs, maintenance state, and webhook reconnect readiness.
- `rob webhook preview` is read-only and shows which Dom/mes would be reissued a new webhook URL.
- `rob webhook send` rotates webhook secrets, rebuilds the URL from `THRONE_WEBHOOK_BASE_URL`, and DMs the guided reconnect flow.
- `rob clear rob_dev_v2` only prints SQL for manual review. It does not execute anything and preserves `db_build_version`.

## DB build vs data migration

- DB schema build is manual SQL in `db/build/` (run as `doadmin`).
- Runtime grants are environment-specific SQL in `db/grants/`.
- SQLite -> PostgreSQL data migration tooling is in `ops/migration/`.
- Deploy scripts do not run schema build SQL.

## Maintenance behavior

During maintenance:

- send tracker posting is paused;
- leaderboard refresh/posting is paused;
- webhook/manual sends still enter the DB as queued work;
- counting remains active;
- manual send-add admin actions remain allowed;
- `/register domme` and `/register sub` are blocked until maintenance ends.

## Deploy gate

Deploy scripts run `ops/checks/check_db.py` and stop on failure with:

> Database check failed.  
> This database has not been built for Rob v2 yet, or runtime grants are incomplete.  
> Run `001_core_schema.sql`, `002_indexes.sql`, `003_achievements.sql`, `004_sub_send_names.sql`, `005_count_recovery.sql`, `006_send_change_requests.sql`, and the correct runtime grants SQL, then rerun deploy.
