# Public API (`api.robthebot.com`)

A small, read-only aiohttp service that powers the website's **home-page stats**
and **"grab your data"** page. It runs as its own process (`apps.public.main`)
with a **SELECT-only** database role — never the webhook writer role.

- Entry point: `python -m apps.public.main`
- App factory: `rob/publicapi/app.py` → `create_public_api_app`
- Handlers: `rob/publicapi/sends.py`, `rob/publicapi/summary.py`
- Read models: `rob/database/repositories/public_sends.py`, `public_summary.py`
- DB role: `db/grants/prod_rob_public.sql` (`prod_rob_public`, SELECT-only)
- Deploy: `deploy/systemd/rob-public.service`, `deploy/env/public.prod.env.example`

## Configuration

| Env var | Default | Notes |
| --- | --- | --- |
| `DATABASE_URL` | — (required) | Point at the SELECT-only `prod_rob_public` role. |
| `PUBLIC_API_HOST` | `127.0.0.1` | Bind host; keep local behind Cloudflare Tunnel. |
| `PUBLIC_API_PORT` | `8090` | Bind port; do not expose publicly. |
| `PUBLIC_API_ALLOWED_ORIGIN` | `https://robthebot.com` | CORS allow-list (see below). |

The frontend base URL lives in the website repo (`src/lib/api.ts`, `API_BASE`,
default `https://api.robthebot.com`).

## CORS

`PUBLIC_API_ALLOWED_ORIGIN` is a **comma-separated allow-list**. Each entry may be:

- an exact origin — `https://robthebot.com`
- a wildcard pattern — `https://*.lovableproject.com` (matched with fnmatch)
- `*` — allow any origin (use while iterating)

A request whose `Origin` matches is **reflected** back in
`Access-Control-Allow-Origin` (with `Vary: Origin`), so the production site and
an ephemeral preview host can both be served by one deployment, e.g.:

```
PUBLIC_API_ALLOWED_ORIGIN=https://robthebot.com,https://*.lovableproject.com
```

Every response (including 400/404) carries the CORS headers, and `OPTIONS`
preflight returns `204`. Both endpoints allow `GET, OPTIONS`.

## `GET /public/guild-summary`

Aggregate stats for the home page. No parameters; scoped to the main guild.
Always **200** (empty data → zeros / `null`).

```json
{
  "last_updated": "2026-07-14T12:34:56Z",
  "total_count": 1234,
  "domme_count": 42,
  "sub_count": 300,
  "totals": [
    { "currency": "USD", "amount_cents": 1234567, "count": 1200 }
  ],
  "top_receivers": [
    { "domme_display_name": "Miss X", "amount_cents": 500000, "currency": "USD", "count": 120 }
  ]
}
```

- `last_updated` = `MAX(sent_at)` across counted sends (`null` if none).
- `total_count` = number of counted sends.
- `totals` = one row per currency, largest total first.
- `domme_count` = dommes with `leaderboard_visible = true` and
  `profile_status = 'active'`; `sub_count` = subs with `profile_status = 'active'`.
- `top_receivers` = top 10 by summed `amount_cents`, grouped by domme + currency,
  labelled the same way as `domme_display_name` below. No Discord ids.

## `GET /public/sends?username={throne_username}`

Returns every **counted send** for a Rob user, scoped to the main guild
(`1485460387355820034`). The `username` is matched case-insensitively against
**both**:

- `sends.sub_name` — the **sub** who *sent* (their sends), and
- `dommes.throne_handle` — the **Dom/me** who *received* (sends to them).

Throne usernames are globally unique, so a name resolves to one person; a Dom/me
who also sends sees both. `resolved_display_name` is the Dom/me's registered
label when the username is a Dom/me handle, otherwise the stored casing of the
most recent matching send.

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

- `resolved_display_name` — see the matching note above (Dom/me label, else the
  most recent send's stored casing).
- `totals` are grouped per currency, largest total first.
- Timestamps are UTC ISO-8601 with a trailing `Z`.

### Guarantees

- **No Discord identifiers** in any response: `sub_user_id`, `domme_user_id`,
  `webhook_secret`, numeric send ids, etc. are never selected into the payload.
  `domme_display_name` comes from `dommes.public_display_name` → `throne_handle`
  → `"Registered Dom/me"` (same precedence as the leaderboard).
- `public_send_id` is always a string. Legacy rows with a NULL
  `public_send_id` get a deterministic id identical to the one the bot would
  persist (see `rob.utils.send_ids.build_public_send_id`).
- **CORS**: see the [CORS](#cors) section — every response (including 400/404)
  carries `Access-Control-Allow-Origin`; `OPTIONS` preflight returns `204`.

### Other routes

- `GET /health` → `{ "ok": true }` (503 if the DB health check fails).

## Deployment notes

Mirror the webhook service: install under `/opt/rob-public/app`, drop the
systemd unit, and front it with a Cloudflare Tunnel for `api.robthebot.com`
(keep `127.0.0.1:8090` local — do not open the port publicly). The runtime DB
role must be `prod_rob_public` (SELECT-only); apply
`db/grants/prod_rob_public.sql` as `doadmin`.
