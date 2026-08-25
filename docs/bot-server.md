# Bot Server

The bot server is the Discord-only side of Rob.

## Responsibilities

- Connects to Discord.
- Connects to PostgreSQL.
- Accepts send notifications from the webhook through the private bot ops bridge.
- Processes the specific recorded send immediately instead of constantly polling pending sends.
- Releases `queued_maintenance` sends after maintenance is disabled.
- Posts send notifications to the configured tracking channel.
- Refreshes leaderboard messages from posted sends.
- Ignores imported legacy sends that were already marked `posted`.
- Handles `/register`, `/leaderboard`, `/add`, and `/count set`.
- Runs the counting listener.

## Runtime

- Entry point: `python -m apps.bot.main`
- Background worker: `rob.services.send_queue_service.SendQueueService`
- PostgreSQL is the source of truth for queue state and maintenance state.
- The queue worker processes send IDs pushed by the webhook and only uses slow fallback checks for maintenance/ops requests.

## Required environment

- `APP_ENV`
- `LOG_LEVEL`
- `DATABASE_URL`
- `DISCORD_TOKEN`
- `BOT_NAME`
- `ROB_OPS_HOST`
- `ROB_OPS_PORT`
- `ROB_OPS_SECRET`

The bot does not host the Throne webhook HTTP server.

## Runtime verification

Run this on the bot host after editing `.env` or after a deploy:

```bash
sudo bash deploy/scripts/check-bot-runtime.sh
```

It validates the parsed bot settings, DB grants/schema, systemd state, the
local bot-ops health endpoint, and the bot's webhook-notify bridge settings.

## Webhook-to-bot send notifications

The bot ops bridge listens on `ROB_OPS_HOST:ROB_OPS_PORT` (default
`127.0.0.1:8811`). The webhook server notifies it as soon as a send is recorded
so the Discord card posts immediately. Keep this bridge on a **private** network
— never expose port `8811` to the public internet, and do **not** put a
public reverse-proxy route in front of `/ops/...`. The ops API can block users,
edit sends, and reissue webhooks, so it must never be reachable from the edge.

Pick whichever matches your topology:

- **Single host** (bot + webhook on one machine): leave the bridge on
  `127.0.0.1:8811` and point the webhook at
  `ROB_BOT_NOTIFY_URL=http://127.0.0.1:8811/ops/sends/process`.
- **Separate hosts**: bind the bridge to a private interface the webhook host
  can reach (e.g. a VPC/WireGuard address such as `ROB_OPS_HOST=10.100.0.2`) and
  set `ROB_BOT_NOTIFY_URL=http://10.100.0.2:8811/ops/sends/process`. Restrict the
  port to the webhook host with a firewall/security group.

In all cases set a strong shared `ROB_OPS_SECRET` on both services: the webhook
sends it as the `X-Rob-Ops-Secret` header. As a safety net the bot **rejects
every ops request** when the bridge is bound off-loopback without a secret (and
logs an error at startup), so a missing secret fails closed rather than serving
the ops API unauthenticated.
