# Rob Achievements

Rob achievements are definition-driven in code, with per-user unlock state stored in PostgreSQL.

## Where things live

- Definitions: `rob/achievements/definitions.py`
- Unlock/query service: `rob/achievements/service.py`
- Achievement card renderers: `rob/achievements/embeds.py`
- Runtime command handlers: `rob/discord/cogs/achievements.py`
- DB tables:
  - `user_achievements`
  - `achievement_events`

## Categories

- `count`
- `sends_domme`
- `sends_sub`
- `leaderboard`
- `throne_tracking`
- `inactivity`
- `maintenance`
- `misc`
- `secret`

## Manual DB setup

Run manually as `doadmin`:

1. Ensure base DB build scripts are already applied:
   - `db/build/001_core_schema.sql`
   - `db/build/002_indexes.sql`
2. Apply achievements and current rehearsal extensions:
   - `db/build/003_achievements.sql`
   - `db/build/004_sub_send_names.sql`
   - `db/build/005_count_recovery.sql`
   - `db/build/006_send_change_requests.sql`
3. Re-run the relevant grants file:
   - dev rehearsal: `db/grants/dev_rehearsal_prod_roles.sql`
   - prod bot: `db/grants/prod_rob_bot.sql`
   - prod webhook: `db/grants/prod_rob_webhook.sql`
4. Validate with runtime credentials:
   - `PYTHONPATH=. python3 -m ops.checks.check_db`

If `ops.checks.check_db.py` reports achievement tables missing, apply the SQL manually and rerun the check.

## Slash commands

- `/achievements` → your achievement cabinet in the current server
- `/achievements scope:server` → server-wide achievement stats for the current server
- `/achievements user:@user` → another member's cabinet in the current server
- `/test achievements` (staff/dev only; visual preview only, no DB writes)

`/test achievements` sends preview cards for every configured achievement so copy/layout can be reviewed in Discord.

## Unlock announcement behavior

- Achievement unlocks are announced in the same context that caused the unlock when that flow supports announcements.
- Unlock announcements now mention the unlocked user directly so Discord actually pings them.
- The footer text is:

  `Achievements Unlock by {display_name}`

- `/achievements` itself remains a view command and should not start pinging people just because someone opens the catalogue.

## Adding or removing achievements

`rob/achievements/definitions.py` is now the catalogue and trigger registry.

1. Add, edit, or remove the definition in `rob/achievements/definitions.py`.
2. For threshold-based sends/counting milestones, stop there — services now read the catalogue and unlock matching entries automatically.
3. For event/manual achievements, keep the explicit `unlock_achievement(...)` call but only reference the key that already lives in the catalogue.
4. Update focused tests if the user-facing copy or trigger semantics changed.

Each definition carries the stable DB key, category, rarity, hidden flag, trigger type/value, icon, and default source label.

## Current trigger notes

- Count/send/leaderboard/throne setup + test webhook + DM + achievements-view triggers are wired.
- Some definitions remain TODO-gated until deeper event history exists (for example some regain/recovery edge cases).
