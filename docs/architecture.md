# Architecture

Bill has two runtimes and one durable data boundary.

```text
Throne --signed webhook--> Cloudflare Worker --SQL--> D1
Discord bot --bearer API--> Cloudflare Worker
Discord bot <--leased notifications-- Cloudflare Worker
```

## Cloudflare Worker

The Worker owns guild configuration, Dom/me registrations, normalized Throne
events, guild send projections, and notification delivery state. Public access
is limited to `/health` and the Throne webhook route.

Bot routes require `Authorization: Bearer <BILL_BOT_API_TOKEN>`. The Throne
route requires both a per-creator secret in the URL and a current Ed25519
signature. Only the route-secret hash is stored.

A Throne event is recorded once, then projected into every configured guild in
which that creator is registered. Event IDs, order IDs, and a stable fallback
hash make webhook retries idempotent.

## Discord bot

The bot owns Discord interactions and message delivery only. It configures
guilds and registrations through the Worker API, then polls for leased send
notifications. A stable message nonce and footer marker let a retry reconcile a
post that reached Discord before its acknowledgement reached the Worker.

Discord guild, channel, user, and message IDs remain decimal strings across the
API and in D1. They are converted to Python integers only when calling Discord.

## Failure behavior

- Invalid or stale Throne signatures are rejected before payload parsing.
- Duplicate Throne events return success without creating duplicate sends.
- Notification leases expire so another bot instance can recover abandoned
  work.
- Permanent channel or permission failures are dead-lettered; transient errors
  are retried with backoff.
- Secrets, raw webhook bodies, and full payloads are never logged.
