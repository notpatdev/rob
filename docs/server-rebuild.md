# Server Rebuild Plan (Rob v2)

This rebuild keeps the Discord bot service and webhook service, but resets DB assumptions for production-shaped PostgreSQL runtime.

## Included

- PostgreSQL-first runtime (`rob_dev_v2` rehearsal, `rob_prod` target)
- manual DB build scripts under `db/build/`
- runtime DB check gate via `ops/checks/check_db.py`
- SQLite -> PostgreSQL import tooling under `ops/migration/`
- webhook compatibility route plus preferred `/webhook/{creator_id}/{secret}`

## Removed intentionally

- Django portal
- `/privacy`
- `/sendrequest`
- manual broadcast command flow
- deploy-time schema creation/migration scripts

## Deploy behavior

Deploy scripts now:

1. sync code
2. install dependencies
3. run compile checks
4. run `ops/checks/check_db.py`
5. restart services

If DB check fails, deploy stops and asks for manual DB build SQL application.

## First rehearsal flow (`rob_dev_v2`)

1. Connect to `rob_dev_v2` as `doadmin`.
2. Run:
   - `db/build/001_core_schema.sql`
   - `db/build/002_indexes.sql`
   - `db/build/003_achievements.sql`
   - `db/build/004_sub_send_names.sql`
   - `db/build/005_count_recovery.sql`
   - `db/build/006_send_change_requests.sql`
3. Apply dev runtime grants:
   - `db/grants/dev_rehearsal_prod_roles.sql`
4. Set runtime `DATABASE_URL` to `prod_rob_bot -> rob_dev_v2` for bot and `prod_rob_webhook -> rob_dev_v2` for webhook.
5. Run `PYTHONPATH=. python3 -m ops.checks.check_db`.
6. Run importer inspect-only.
7. Run importer dry-run.
8. Run actual import into `rob_dev_v2`.
9. Validate row counts + send totals against the inspection report.
