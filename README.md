# Bill

Bill is a multi-server Discord bot with durable member profiles and verified
Throne send tracking.

## Architecture

- **Discord bot:** Python 3.12 on DigitalOcean. Members use `/profile`; server
  administrators configure the send channel with `/bill setup`.
- **Webhook and data API:** native TypeScript Cloudflare Worker at
  `usebill.dev`.
- **Database:** Cloudflare D1. The bot never connects to D1 directly.

`billthebot.xyz` is reserved for Bill's future website. There is no website in
this milestone.

## Local checks

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
ruff check bill tests
pytest -q

cd worker
npm ci
npm run check
```

See the [codebase guide](docs/codebase-guide.md), [profiles](docs/profiles.md),
[deployment](docs/deployment.md), [architecture](docs/architecture.md), and
[send tracking](docs/send-tracking.md).

## Current scope

Included: global and per-server profiles, durable DM onboarding, safe static
link-page import, optional Throne connection, alias attribution for future
sends, public per-currency stats, multi-server setup, authenticated Worker APIs,
signed/idempotent Throne ingestion, D1 persistence, and leased notifications.

Leaderboards, reports, moderation, manual sends, support tooling, diagnostics
commands, and a website remain out of scope.
