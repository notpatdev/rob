# Database Build (Manual SQL)

Rob v2 schema build is manual and admin-driven.

## Important terminology

- **DB build scripts**: SQL files that create/alter schema.
- **Data migration**: moving legacy SQLite data into PostgreSQL.

Only SQLite -> PostgreSQL transfer should be called a migration.

## Run order

Execute as `doadmin` in pgAdmin4 or `psql`:

1. `db/build/001_core_schema.sql`
2. `db/build/002_indexes.sql`
3. `db/build/003_achievements.sql`
4. `db/build/004_sub_send_names.sql`
5. `db/build/005_count_recovery.sql`
6. `db/build/006_send_change_requests.sql`
7. `db/build/003_runtime_grants_template.sql` (optional reference template)

Then apply runtime grants:

- Dev rehearsal using production-shaped roles: `db/grants/dev_rehearsal_prod_roles.sql`
- Prod bot: `db/grants/prod_rob_bot.sql`
- Prod webhook: `db/grants/prod_rob_webhook.sql`

Required `db_build_version` rows are:

- `001_core_schema`
- `002_indexes`
- `003_achievements`
- `004_sub_send_names`
- `005_count_recovery`
- `006_send_change_requests`

Runtime grants are environment-specific and are validated by `ops/checks/check_db.py` from runtime credentials.

## Canonical rehearsal order (`rob_dev_v2`)

1. Ensure `prod_rob_bot` and `prod_rob_webhook` roles exist.
2. Run `db/build/001_core_schema.sql`.
3. Run `db/build/002_indexes.sql`.
4. Run `db/build/003_achievements.sql`.
5. Run `db/build/004_sub_send_names.sql`.
6. Run `db/build/005_count_recovery.sql`.
7. Run `db/build/006_send_change_requests.sql`.
8. Run `db/grants/dev_rehearsal_prod_roles.sql`.
9. Run `PYTHONPATH=. python3 -m ops.checks.check_db` from both bot and webhook runtime environments.

## Target tables

- `db_build_version`
- `bot_settings`
- `bot_users`
- `dommes`
- `subs`
- `sub_send_names`
- `sends`
- `vib_settings`
- `vib_leaderboard`
- `the_count`
- `inactive_users`
- `count_recovery_windows`
- `count_blocks`
- `send_change_requests`
- `user_achievements`
- `achievement_events`

## Runtime safety

Runtime users (`prod_rob_bot`, `prod_rob_webhook`) must not receive:

- `CREATE`
- `ALTER`
- `DROP`
- `TRUNCATE`

Use `ops/checks/check_db.py` from runtime credentials to validate permissions and table/column shape.
