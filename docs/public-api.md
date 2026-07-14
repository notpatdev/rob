# Public API (`api.robthebot.com`)

A small, read-only aiohttp service that powers the website's **"grab your data"**
page. It runs as its own process (`apps.public.main`) with a **SELECT-only**
database role — never the webhook writer role.

- Entry point: `python -m apps.public.main`
- App factory: `rob/publicapi/app.py` → `create_public_api_app`
- Handler: `rob/publicapi/sends.py`
- Read model: `rob/database/repositories/public_sends.py`
- DB role: `db/grants/prod_rob_public.sql` (`prod_rob_public`, SELECT-only)
- Deploy: `deploy/systemd/rob-public.service`, `deploy/env/public.prod.env.example`

## Configuration

| Env var | Default | Notes |
| --- | --- | --- |
| `DATABASE_URL` | — (required) | Point at the SELECT-only `prod_rob_public` role. |
| `PUBLIC_API_HOST` | `127.0.0.1` | Bind host; keep local behind Cloudflare Tunnel. |
| `PUBLIC_API_PORT` | `8090` | Bind port; do not expose publicly. |
| `PUBLIC_API_ALLOWED_ORIGIN` | `https://robthebot.com` | CORS origin. Use `*` only while testing. |

The frontend base URL lives in the website repo (`src/lib/api.ts`, `API_BASE`,
default `https://api.robthebot.com`).

## `GET /public/sends?username={throne_username}`

Returns every **counted send** for a Rob user, matched case-insensitively
against `sends.sub_name` and scoped to the main guild
(`1485460387355820034`).

A **counted send** (same definition as the leaderboard) is:

- `discord_post_status = 'posted'`
- `is_private = false`
- `is_test_send` is not true

Returns **404** when no counted sends match.

### Response

```json
{
  "username": "someone",
  "resolved_display_name": "Someone",
  "last_updated": "2026-07-10T00:00:00Z",
  "total_count": 3,
  "totals": [
    { "currency": "USD", "amount_cents": 12500, "count": 3 }
  ],
  "recent": [ /* SendRow, newest first, max 5 */ ],
  "all_sends": [ /* SendRow, newest first, every counted send */ ]
}
```

`SendRow`:

```json
{
  "public_send_id": "ROB-000123-ABCD1234",
  "sent_at": "2026-07-10T00:00:00Z",
  "amount_cents": 2500,
  "currency": "USD",
  "domme_display_name": "Miss X",
  "item_name": "Coffee",
  "sub_name": "someone"
}
```

- `resolved_display_name` is the stored casing of the most recent matching send.
- `totals` are grouped per currency, largest total first.
- Timestamps are UTC ISO-8601 with a trailing `Z`.

### Guarantees

- **No Discord identifiers** in any response: `sub_user_id`, `domme_user_id`,
  `webhook_secret`, numeric send ids, etc. are never selected into the payload.
  `domme_display_name` comes from `dommes.public_display_name` → `throne_handle`
  → `"A Dom/me"`.
- `public_send_id` is always a string. Legacy rows with a NULL
  `public_send_id` get a deterministic id identical to the one the bot would
  persist (see `rob.utils.send_ids.build_public_send_id`).
- **CORS**: every response (including 400/404) carries
  `Access-Control-Allow-Origin`; `OPTIONS` preflight returns `204`.

### Other routes

- `GET /health` → `{ "ok": true }` (503 if the DB health check fails).

## Deployment notes

Mirror the webhook service: install under `/opt/rob-public/app`, drop the
systemd unit, and front it with a Cloudflare Tunnel for `api.robthebot.com`
(keep `127.0.0.1:8090` local — do not open the port publicly). The runtime DB
role must be `prod_rob_public` (SELECT-only); apply
`db/grants/prod_rob_public.sql` as `doadmin`.
