# Deployment

Bill uses a Cloudflare Worker/D1 database and a Python bot on DigitalOcean.
Examples contain no production IDs or secrets.

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
