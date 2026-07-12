# Dev Server Report (Prod-Role Rehearsal)

This report tracks the current rehearsal layout for Rob v2.

## Active service split

- Bot host:
  - app: `/opt/rob-bot/app`
  - service: `rob-bot-dev.service`
  - runtime DB user: `prod_rob_bot`
- Webhook host:
  - app: `/opt/rob-webhook/app`
  - service: `rob-webhook-dev.service`
  - runtime DB user: `prod_rob_webhook`

Both services currently target `rob_dev_v2` for rehearsal.

## Webhook routing

- Public hostname: `https://throne.robthebot.com`
- Local origin: `http://127.0.0.1:8080`
- Route should be exposed through Cloudflared, not direct firewall exposure.

Do not expose port `8080` publicly.

## Runtime env direction

Webhook `.env` must not include `DISCORD_TOKEN`.

Bot `.env` must include `DISCORD_TOKEN`.

Both `.env` files should use production-shaped runtime DB users against `rob_dev_v2` until cutover to `rob_prod`.

## Deploy direction

Deploy scripts and prechecks:

- never run DB build SQL automatically;
- never run SQLite import automatically;
- validate schema/permissions with `ops.checks.check_db`;
- restart only the target service.

Manual DB build and grants stay admin-only via pgAdmin4 / `psql` as `doadmin`.
