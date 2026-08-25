# Deployment

The old split deploy workflows have been replaced by a single workflow: **Deploy Rob Codebase** in `.github/workflows/deploy-codebase.yml`.

<<<<<<< HEAD
## 1. Prepare the Worker

```bash
cd worker
npm ci
npx wrangler login
```

`worker/wrangler.toml` already contains the production D1 identifier
`6333cb0a-0c23-44b2-9022-a6fde1500f77` and
`PUBLIC_BASE_URL=https://usebill.dev`. Do not create or substitute another
database for the live rollout.

Set required Worker deployment values. `BILL_HOME_GUILD_ID` is configuration,
not an application default; enter the real Discord snowflake interactively:

```bash
npx wrangler secret put BILL_HOME_GUILD_ID
npx wrangler secret put BILL_BOT_API_TOKEN
npx wrangler secret put THRONE_PUBLIC_KEY_PEM
```

## 2. Apply and deploy in order

Run the checks, then apply every pending additive migration to the existing
database before deploying code that uses the new tables:

```bash
cd worker
npm ci
npm run check
npx wrangler d1 migrations apply bill --remote
npx wrangler deploy
```

The exact live order is: Worker `BILL_HOME_GUILD_ID`, D1 migrations, Worker
deploy, bot `BILL_HOME_GUILD_ID`, bot restart. Migrations `0002` and `0003` are
additive over populated `0001`; do not edit or re-run `0001` manually.

Use a long randomly generated bot API token. Set `THRONE_PUBLIC_KEY_PEM` to
Throne's current Ed25519 public key.

Route `usebill.dev` to the Worker in Cloudflare. Keep `billthebot.xyz` reserved
for Bill's future product website; no website is deployed in this milestone.

For local Worker development, copy `worker/.dev.vars.example` to
`worker/.dev.vars`, use development-only values, apply migrations locally, and
run `npm run dev`.

## 3. Install the Discord bot

On the DigitalOcean host:

```bash
sudo install -d -o bill -g bill /opt/bill
sudo -u bill git clone <repository-url> /opt/bill
sudo -u bill python3 -m venv /opt/bill/.venv
sudo -u bill /opt/bill/.venv/bin/pip install /opt/bill
sudo install -d -m 0750 /etc/bill
sudo install -m 0640 deploy/bill-bot.env.example /etc/bill/bill-bot.env
sudo install -m 0644 deploy/bill-bot.service /etc/systemd/system/bill-bot.service
```

Edit `/etc/bill/bill-bot.env` with the Discord token, Worker URL, the same bot
API token stored in Wrangler, and the same real `BILL_HOME_GUILD_ID`. Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now bill-bot
sudo systemctl status bill-bot
```

## 4. Configure Discord

Invite Bill with the `bot` and `applications.commands` scopes. Grant **View
Channel**, **Send Messages**, **Embed Links**, and **Read Message History** in
the send channel. In each server:

1. A member with **Manage Server** runs `/bill setup` and completes the public
   channel-selection flow.
2. Members run `/profile` to create the applicable profile.
3. Dom/me and switch profiles may connect Throne in the private DM wizard.
4. Add any newly issued private webhook URL to Throne.
5. Use Throne's webhook test and confirm that a real supported event posts once.

Never paste a webhook URL into a public channel, issue, log, or deployment
output. Bill can show a new URL only on initial issue or explicit rotation.
=======
The canonical Rob repo is now `foolishbuilder/rob`. Any earlier rehearsal/bootstrap repo references should be treated as legacy history, not as the live source of truth, and not as a code-history merge from the legacy `notpatdev/robthebot` repository.

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

1. Clone from `https://github.com/foolishbuilder/rob.git`.
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
>>>>>>> parent of a3023d4 (Rebuild Bill send tracking)
