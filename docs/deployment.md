# Deployment

Bill uses a Cloudflare Worker/D1 database and a Python bot on DigitalOcean.
Examples contain no production IDs or secrets.

## 1. Create and deploy the Worker

```bash
cd worker
npm ci
npx wrangler login
npx wrangler d1 create bill
```

Copy the returned database ID into `wrangler.toml`, then apply the migration:

```bash
npx wrangler d1 migrations apply bill --remote
```

Set secrets:

```bash
npx wrangler secret put BILL_BOT_API_TOKEN
npx wrangler secret put THRONE_PUBLIC_KEY_PEM
```

Use a long randomly generated bot API token. Set `THRONE_PUBLIC_KEY_PEM` to
Throne's current Ed25519 public key. Deploy:

```bash
npm run check
npx wrangler deploy
```

Route `usebill.dev` to the Worker in Cloudflare. Keep `billthebot.xyz` reserved
for Bill's future product website; no website is deployed in this milestone.

For local Worker development, copy `worker/.dev.vars.example` to
`worker/.dev.vars`, use development-only values, apply migrations locally, and
run `npm run dev`.

## 2. Install the Discord bot

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

Edit `/etc/bill/bill-bot.env` with the Discord token, Worker URL, and the same
bot API token stored in Wrangler. Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now bill-bot
sudo systemctl status bill-bot
```

## 3. Configure Discord

Invite Bill with the `bot` and `applications.commands` scopes. Grant **View
Channel**, **Send Messages**, **Embed Links**, and **Read Message History** in
the send channel. In each server:

1. Run `/bill setup`.
2. Run `/register domme`.
3. Add the private webhook URL to Throne.
4. Use Throne's webhook test and confirm that a real supported event posts once.
