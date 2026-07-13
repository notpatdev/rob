# Deployment

The old split deploy workflows have been replaced by a single workflow: **Deploy Rob Codebase** in `.github/workflows/deploy-codebase.yml`.

The canonical Rob repo is now `patdadev/rob`. Any earlier rehearsal/bootstrap repo references should be treated as legacy history, not as the live source of truth, and not as a code-history merge from the legacy `notpatdev/robthebot` repository.

## Deployment flow

1. GitHub runs codebase checks first (compile, ruff, pytest, deploy file sanity checks).
2. Bot server pre-check runs over SSH before any bot deploy.
3. Webhook server pre-check runs over SSH before any webhook deploy.
4. Bot deploy runs only if bot pre-check passes.
5. Webhook deploy runs only if webhook pre-check passes.

## Triggering

- Push to `main` deploys **dev** automatically.
- Manual `workflow_dispatch` can deploy `dev` or `prod`.
- `prod` should be protected using GitHub Environment approval rules.
- Bot and webhook can be deployed independently with workflow inputs.

## Safety and scope

Deployment does **not**:

- build DB schema automatically;
- run SQLite data migration automatically;
- use doadmin runtime credentials;
- overwrite `.env`;
- print secrets.

Deployment pre-check and deploy scripts validate DB readiness via `ops/checks/check_db.py`, but do not mutate schema.

## Manual DB build remains separate

If schema build/grants are required, run manually (admin action):

- `db/build/001_core_schema.sql`
- `db/build/002_indexes.sql`
- `db/build/004_sub_send_names.sql`
- `db/build/005_count_recovery.sql`
- `db/build/006_send_change_requests.sql`
- `db/build/007_send_update_requests.sql`
- `db/build/008_dm_preferences.sql`
- `db/build/009_terms_acceptance.sql`
- `db/grants/*.sql`

SQLite data migration remains separate and is not part of deployment.

## Repo bootstrap guidance

When bootstrapping a fresh host or validating a fresh checkout:

1. Clone from `https://github.com/patdadev/rob.git`.
2. Copy Actions secrets, environments, and protection rules into the active repo if GitHub is being rebuilt.
3. Verify workflow wiring in the active repo before deploy.
4. Rehearse services and imported data against `rob_dev_v2` if you are doing a migration dry run.
5. Only then proceed with `main`-based deployment to production.

## Production install path

For production, use:

- Bot installer: `deploy/scripts/install-bot.sh`
- Webhook installer: `deploy/scripts/install-webhook.sh`
- Bot service: `rob-bot.service`
- Webhook service: `rob-webhook.service`
- Production database: `rob_prod`
- Runtime users: `prod_rob_bot` and `prod_rob_webhook`

Current production examples live in:

- `deploy/env/bot.prod.env.example`
- `deploy/env/webhook.prod.env.example`

The webhook host should stay on `127.0.0.1:8080` behind Cloudflared, and it should notify the bot over the private ops bridge (`ROB_BOT_NOTIFY_URL`) instead of polling the database for send cards.

If either service points at an older database, `ops/checks/check_db.py` will fail because Rob v2 expects `db_build_version` and the new v2 schema tables.

## Manual DB bootstrap

Production DB setup remains manual. Use:

- `db/manual/setup_rob_prod.sql`

That script:

- creates `prod_rob_bot` and `prod_rob_webhook` if they do not already exist;
- creates `rob_prod` if it does not already exist;
- runs the full manual DB build order;
- applies the production grants files.

Run it manually as `doadmin`, for example:

```bash
psql postgresql://doadmin@<host>:25060/defaultdb \
  -v prod_rob_bot_password='replace-me' \
  -v prod_rob_webhook_password='replace-me-too' \
  -f db/manual/setup_rob_prod.sql
```

## Infrastructure hostnames

- `bot-01.robthebot.com`
- `webhook-01.robthebot.com`
- `db-01.robthebot.com`

`db-01.robthebot.com` is a private/internal/admin-only reference by default. Do not expose PostgreSQL publicly unless protected by strict network controls.

The webhook service should stay on `127.0.0.1:8080` behind Cloudflared; do not expose port `8080` publicly.
