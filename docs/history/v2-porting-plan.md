# Rob v2 Legacy Porting Plan

This document maps the legacy ROB feature inventory (`ROB_FEATURE_REPORT.md`) to the current Rob v2 split-runtime architecture (Discord bot + webhook server + PostgreSQL + Components V2).

## Porting matrix

### 1) Bot startup and lifecycle
- **Legacy behaviour:** config/env load, DB init/migrations, cog registration, app command sync, webhook lifecycle hooks, global blacklist checks on slash/prefix.
- **Legacy modules:** `archive/single-process-bot/config.py`, bot bootstrap/runtime files.
- **Current v2 status:** Partial.
- **Classification:** Partial.
- **Port?** Yes.
- **Suggested v2 modules:** `apps/bot/main.py`, `rob/discord/client.py`, `rob/discord/checks.py`, `rob/services/blacklist_service.py`, `rob/database/repositories/blacklist.py`.
- **Migrations:** none.
- **Services/repos:** blacklist parity hardening.
- **Tests:** startup smoke + deny response regression tests.
- **Priority:** P0 startup/P1 blacklist parity.
- **Notes/risks:** keep deny responses non-leaky.

### 2) Throne webhook ingestion
- **Legacy behaviour:** signed webhook verification, secret validation, dedupe, extraction, send posting + leaderboard sync.
- **Legacy modules:** `archive/single-process-bot/webhook_server.py`, `throne_tracker.py`.
- **Current v2 status:** Partial (core exists).
- **Classification:** Partial.
- **Port?** Yes.
- **Suggested v2 modules:** `apps/webhook/main.py`, `rob/throne/webhooks.py`, `rob/throne/security.py`, `rob/throne/payloads.py`, `rob/services/send_service.py`, `rob/services/send_queue_service.py`.
- **Migrations:** ensure dedupe keys are indexed.
- **Services/repos:** send repo dedupe + queue transitions.
- **Tests:** signature, secret mismatch, dedupe, payload mapping for `gift_purchased` / `contribution_purchased` / `gift_crowdfunded` with direct minor units.
- **Priority:** P0.
- **Notes/risks:** no runtime SQLite and no bot/webhook merge.

### 3) Test webhook / dev test-send override
- **Status:** Partial; behaviour retained and documented.
- **Priority:** P0.

### 4) Registration flows
- **Status:** Partial.
- **Priority:** P0.
- **Needs:** stronger copy parity, historical claim checks, setup verification flow polish.

### 5) Send tracking cards
- **Status:** Partial.
- **Priority:** P0.
- **Needs:** old-Rob tone + title/body/claim-state/rank line + currency friendly names.

### 6) Leaderboards (2-message design)
- **Status:** Partial.
- **Priority:** P0.
- **Needs:** exactly main board + stats panel, no separate sub leaderboard message, plus future explicit offline source if desired.

### 7) Send requests
- **Status:** Partial.
- **Priority:** P0/P1.
- **Needs:** DM approve/ignore actions + rate limits.

### 8) Manual send logging
- **Status:** Partial.
- **Priority:** P0/P1.
- **Needs:** legacy method aliases and pipeline parity.

### 9) Counting system
- **Status:** Partial.
- **Priority:** P0/P1.
- **Needs:** restore windows, admin fix/status parity.

### 10) Maintenance mode
- **Status:** Partial.
- **Priority:** P0.
- **Needs:** robctl parity + queue flush/release UX.

### 11) Inactivity removal
- **Status:** Missing.
- **Priority:** P1.
- **Safety:** dry-run default, opt-in destructive mode only.

### 12) DM audit forwarding
- **Status:** Missing.
- **Priority:** P2/P3.

### 13) Carl-bot warn relay
- **Status:** Missing.
- **Priority:** P2.

### 14) Rule helper
- **Status:** Missing.
- **Priority:** P1/P2.

### 15) Blacklist systems
- **Status:** Partial.
- **Priority:** P1.

### 16) Local webhook/admin endpoints
- **Status:** Partial.
- **Priority:** P1/P2.

### 17) Shell helpers / robctl parity
- **Status:** Partial.
- **Priority:** P1.

### 18) Event runtime/final reports
- **Status:** Intentionally removed.
- **Priority:** Do not port.
- **Reason:** v2 direction is always-on tracking, not event-window orchestration.

## Summary table

| Feature | Legacy behaviour | v2 status | Port priority | Suggested PR | Notes |
|---|---|---|---|---|---|
| Startup/lifecycle | boot + checks + sync | Partial | P0/P1 | bot-startup-hardening | retain split runtime |
| Throne ingestion | signed webhook + dedupe | Partial | P0 | throne-ingestion-parity | direct minor-unit pricing |
| Test webhook mode | setup verification vs real sends | Partial | P0 | webhook-test-mode | explicit setup payloads never real sends |
| Registration | domme/sub + claim + setup DM | Partial | P0 | registration-parity | keep webhook URL private |
| Send card | rich old-Rob send card | Partial | P0 | send-card-v2 | no embeds |
| Leaderboards | 2 fixed messages + stats | Partial | P0 | leaderboard-main-stats | remove separate sub leaderboard |
| Send requests | DM approve/ignore flow | Partial | P0/P1 | send-request-rebuild | enforce 24h rate limits |
| Manual sends | /add + methods | Partial | P0/P1 | manual-send-parity | alias legacy methods |
| Counting | numeric + restore logic | Partial | P0/P1 | counting-restore-parity | add restore metadata fields |
| Maintenance | queue/release/refresh | Partial | P0 | maintenance-parity | DB-backed state |
| Inactivity | periodic warning/kick | Complete | P1 | inactivity-service | bot-state scheduling + role-based targeting |
| DM audit | forward inbound DMs | Missing | P2/P3 | dm-audit | privacy review required |
| Warn relay | Carl warn -> courtesy DM | Complete | P2 | warn-relay | dedupe implemented |
| Rule helper | !rule topic map | Missing | P1/P2 | rule-command | slash-first acceptable |
| Blacklist | global deny + admin tools | Complete | P1 | blacklist-parity | mod commands + robctl management |
| Local admin endpoints | health/maintenance/sync/broadcast | Partial | P1/P2 | local-admin-surface | loopback only |
| Shell helpers | extensive ops commands | Partial | P1 | robctl-expansion | global `robctl` installer + expanded command matrix |
| Event runtime | event windows/reports | Intentionally removed | Do not port | n/a | only if explicitly requested |

- 2026-05-23: Public send card now uses compact Components V2 layout with real `discord.ui.Separator()`, thumbnails, and purple accent constants from `rob/ui/theme.py`; public send IDs remain intentionally absent from the announcement card.
- 2026-05-23: Added NEW LEADER ALERT runtime posting with bot-state dedupe and maintenance/test-send safeguards.
- 2026-05-23: Leaderboard and stats cards now use explicit separator components, registered Dom/me aggregation, and dynamic live/maintenance status on the main board.
- 2026-05-23: Stored PostgreSQL-backed public send IDs are available for support/admin workflows, with a `robctl sends backfill-public-ids` path for older rows.
- 2026-05-24: Inactivity scheduler, warn-log courtesy relay, and blacklist admin parity were ported to v2; DM audit and `!rule` remain intentionally excluded per current scope.
