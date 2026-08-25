# Webhook Server

The webhook server is the HTTP-only side of Rob.

## Responsibilities

- Receives Throne webhook events.
- Validates the URL secret against `webhook_secret` and `webhook_secret_hash`.
- Optionally validates Throne Ed25519 signatures and timestamps.
- Normalises accepted Throne purchase payloads.
- Writes sends into PostgreSQL.
- Notifies the bot ops bridge after a send is recorded so Discord posting can happen immediately.
- Respects maintenance mode by inserting `queued_maintenance` instead of `pending`.
- Never connects to Discord.

## Runtime

- Entry point: `python -m apps.webhook.main`
- Host/port: `THRONE_WEBHOOK_HOST` / `THRONE_WEBHOOK_PORT`
- Health check: `GET /health`
- Webhook route: `POST /throne/webhook/{creator_id}/{secret}`
- The webhook runtime loads `WebhookSettings` only and can start without `DISCORD_TOKEN`.

## Required environment

- `APP_ENV`
- `LOG_LEVEL`
- `DATABASE_URL`
- `THRONE_WEBHOOK_HOST`
- `THRONE_WEBHOOK_PORT`
- `THRONE_WEBHOOK_BASE_URL`
- `THRONE_WEBHOOK_REQUIRE_SIGNATURE`
- `THRONE_PUBLIC_KEY_PEM`
- `THRONE_WEBHOOK_DEBUG_LOG_PAYLOAD`
- `THRONE_WEBHOOK_TIMESTAMP_HEADER`
- `THRONE_WEBHOOK_SIGNATURE_HEADER`
- `THRONE_WEBHOOK_SIGNED_MESSAGE_FORMAT`
- `THRONE_WEBHOOK_MAX_TIMESTAMP_SKEW_SECONDS`
- `ROB_BOT_NOTIFY_URL` (recommended; a private-network address, e.g. `http://127.0.0.1:8811/ops/sends/process` on a single host or `http://10.100.0.2:8811/ops/sends/process` across hosts)
- `ROB_OPS_SECRET` (must match the bot server when `ROB_BOT_NOTIFY_URL` is set)

`DISCORD_TOKEN` is not required here.

## Runtime verification

Run this on the webhook host after editing `.env` or after a deploy:

```bash
sudo bash deploy/scripts/check-webhook-runtime.sh
```

It validates the parsed webhook settings, DB grants/schema, systemd state,
local `/health`, the public webhook host, and the bot-notify bridge route when configured.

## Bot notification checklist

For sends to appear in Discord without bot-side polling, the webhook server must
reach the bot ops bridge over a **private** network (loopback on a single host,
or a private/VPC address between hosts — never the public internet):

```env
# Single host (bot + webhook together):
ROB_BOT_NOTIFY_URL=http://127.0.0.1:8811/ops/sends/process
# Separate hosts (private address reachable only by the webhook host):
# ROB_BOT_NOTIFY_URL=http://10.100.0.2:8811/ops/sends/process
ROB_OPS_SECRET=<same value as the bot server>
```

That URL must be reachable from the webhook host and resolve to the bot's ops
bridge on the private network. The endpoint is `POST` only, so a plain `GET` may
return `405`; that still proves the route exists. Do not expose `/ops/...`
through a public reverse proxy — see `docs/bot-server.md`.

## `THRONE_WEBHOOK_REQUIRE_SIGNATURE`

- When `THRONE_WEBHOOK_REQUIRE_SIGNATURE=true`, the webhook rejects requests with `401` if the timestamp is invalid, the public key is missing, or the Ed25519 signature check fails.
- When `THRONE_WEBHOOK_REQUIRE_SIGNATURE=false`, the webhook skips Ed25519 signature validation entirely, but it still requires valid JSON plus a matching `{creator_id}/{secret}` URL pair.
- For early local or tunnel-based dev, `false` is acceptable while the real Throne public key and signed-message format are still being verified.
- **Production recommendation:** set `THRONE_WEBHOOK_REQUIRE_SIGNATURE=true` and provide `THRONE_PUBLIC_KEY_PEM` so inbound events are cryptographically verified, not merely gated by the URL secret. The webhook is the one service intentionally exposed to the internet, so the URL secret alone is a weaker guarantee. (The shipped install templates default this to `false`; flip it to `true` once the Throne public key is in place — leaving it `true` without a key makes the webhook reject every event.)
- For stricter shared-dev testing, switch it back to `true` and provide `THRONE_PUBLIC_KEY_PEM` plus the correct header and message-format settings.


## Throne test webhook handling
- Explicit test/setup webhook payloads are detected before send insertion.
- Explicit test/setup events update creator setup verification timestamps (`setup_verified_at`, `last_test_webhook_at`, `last_successful_event_at`) and return `{"ok": true, "setup_verified": true}`.
- Explicit test/setup events do not insert `sends` rows and do not enter the Discord send tracker queue.
- Runtime currently renders registration/setup UI with no embed fallback via a Components V2 compatibility layer until discord.py exposes stable V2 APIs.

- `THRONE_PARSE_TEST_SENDS_AS_REAL_SENDS` (default `false`) allows known test sender usernames to pass as real sends for dev testing.
- `THRONE_TEST_GIFTER_USERNAMES` controls sender-name-based test detection (default `marie_123`).
- Known test senders are still inserted as `sends` rows so the public send tracker flow can render, but those rows are stored with `is_test_send=true`.
- When `THRONE_PARSE_TEST_SENDS_AS_REAL_SENDS=false`, known test-sender rows are excluded from leaderboard totals, stats, and leader alerts unless the recipient matches `THRONE_TEST_SEND_LEADERBOARD_OWNER_USER_ID`.
- If test parsing was previously enabled, `ops/cli/rob throne invalidate-test-sends` can backfill historical known test sender rows to `is_test_send=true`.
- Explicit test/setup payloads are always setup-only and never inserted as sends.
- Webhook payload `price`/`amount` values are treated as authoritative minor currency units for send amounts.
