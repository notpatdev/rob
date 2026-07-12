# Production Rollout Checklist (Rob v2 Rebuild)

## 1) Build schema manually as `doadmin`

Run in order, against `rob_prod`:

1. `db/build/001_core_schema.sql`
2. `db/build/002_indexes.sql`
3. `db/build/003_achievements.sql`
4. `db/build/004_sub_send_names.sql`
5. `db/build/005_count_recovery.sql`
6. `db/build/006_send_change_requests.sql`
7. `db/build/003_runtime_grants_template.sql` (optional reference template)
8. `db/grants/prod_rob_bot.sql`
9. `db/grants/prod_rob_webhook.sql`

These are DB build scripts, not app migrations.

## 2) Confirm runtime users

- `prod_rob_bot` connects and has runtime table/sequence access.
- `prod_rob_webhook` connects with narrower runtime grants.
- Neither runtime user has schema `CREATE`.

## 3) Seed minimum configuration

Insert only required production rows:

- `vib_settings` for production guild(s)
- optional `bot_settings` defaults (maintenance/features/inactivity)

Do not copy dev send history by default.

## 4) Configure runtime environment

Bot:
- `DATABASE_URL=postgresql://prod_rob_bot:.../rob_prod?...`

Webhook:
- `DATABASE_URL=postgresql://prod_rob_webhook:.../rob_prod?...`

No admin DB credential is required in runtime `.env`.

Rehearsal note:

- Use the same role names (`prod_rob_bot`, `prod_rob_webhook`) against `rob_dev_v2` before cutover.

## 5) Validate DB with runtime credentials

Run:

```bash
PYTHONPATH=. python3 -m scripts.check_db
```

If this fails, apply missing DB build SQL manually and rerun.

## 6) Deploy webhook and bot services

Deploy scripts intentionally run:

1. dependency install
2. compile checks
3. `scripts.check_db`
4. service restart

Deploy scripts do **not** create/alter schema.

## 7) Functional validation

1. registration works (`/register domme`, `/register sub`)
2. webhook ingest works on preferred route
3. send queue posts and marks sends
4. leaderboard refresh works
5. `/leaderboard` and counting features behave normally
6. inactivity loop behaves as expected

## 8) Post-cutover audit

- runtime logs show no DB permission errors
- webhook user is still missing schema `CREATE`
- `db_build_version` includes:
  - `001_core_schema`
  - `002_indexes`
  - `003_achievements`
  - `004_sub_send_names`
  - `005_count_recovery`
  - `006_send_change_requests`
