# Rob documentation

Index of the docs in this directory, grouped by topic. Point-in-time
snapshots from the v2 rebuild live under [`history/`](#history).

## Deployment & servers

- [deployment.md](deployment.md) — production deployment overview
- [deployment-combined.md](deployment-combined.md) — single-host (bot + webhook) deployment
- [deployment-bot-dev.md](deployment-bot-dev.md) — bot dev-server deployment
- [deployment-webhook-dev.md](deployment-webhook-dev.md) — webhook dev-server deployment
- [bot-server.md](bot-server.md) — bot service host
- [webhook-server.md](webhook-server.md) — webhook service host
- [cloudflared-webhook.md](cloudflared-webhook.md) — Cloudflare tunnel for the webhook
- [domain-routing.md](domain-routing.md) — domain / routing layout
- [server-rebuild.md](server-rebuild.md) — rebuilding a host from scratch

## Database

- [database-architecture.md](database-architecture.md) — schema & design
- [database-build.md](database-build.md) — building the schema from `db/build/`
- [sqlite-to-postgres-data-migration.md](sqlite-to-postgres-data-migration.md) — legacy SQLite → Postgres migration

## Operations & runbooks

- [operations-runbook.md](operations-runbook.md) — day-to-day operations
- [prod-rollout-checklist.md](prod-rollout-checklist.md) — production rollout checklist
- [maintenance-mode.md](maintenance-mode.md) — maintenance mode (`ops/cli/rob maintenance …`)
- [backend-commands.md](backend-commands.md) — backend / admin commands

## Features & reference

- [discord-commands.md](discord-commands.md) — Discord command reference
- [achievements.md](achievements.md) — achievements system
- [inactivity-and-server-backup.md](inactivity-and-server-backup.md) — inactivity tracking & server backup
- [voice-transcription.md](voice-transcription.md) — voice-message transcription
- [legacy-feature-gap-report.md](legacy-feature-gap-report.md) — v1→v2 feature-gap report (kept here; referenced by tests)

## History

Point-in-time snapshots from the v2 rebuild. Kept for reference; not living runbooks.

- [history/preproduction-issues.md](history/preproduction-issues.md)
- [history/v2-porting-plan.md](history/v2-porting-plan.md)
- [history/feature-parity-audit.md](history/feature-parity-audit.md)
- [history/components-v2-research.md](history/components-v2-research.md)
- [history/dev-server-report.md](history/dev-server-report.md)
