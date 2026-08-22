# Bill

Bill is a multi-server Discord bot that posts verified Throne sends. This first
milestone deliberately contains one complete feature: secure send tracking from
Throne to Discord.

## Architecture

- **Discord bot:** Python 3.12 on DigitalOcean. Administrators choose a send
  channel with `/bill setup`; Dom/mes connect Throne with `/register domme`.
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

## Guides

- [Version control with Git and GitHub](docs/version-control.md)
- [Collaborating with Issues, PRs, reviews, and stacks](docs/github-collaboration.md)
- [Releasing Bill](docs/releases.md)
- [Contributing](CONTRIBUTING.md)
- [Deployment](docs/deployment.md)
- [Architecture](docs/architecture.md)
- [Send tracking](docs/send-tracking.md)

## Current scope

Included: multi-server channel setup, Dom/me Throne registration, authenticated
Worker APIs, signed/idempotent Throne ingestion, D1 persistence, and leased
Discord notification delivery.

Profiles, leaderboards, sub aliases, reports, moderation, manual sends, and a
website are not part of this milestone.
