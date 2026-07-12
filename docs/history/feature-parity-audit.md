# Rob Feature Parity Audit

| Feature | Old bot | New bot before patch | New bot after patch | Status |
|---|---|---|---|---|
| counting | full legacy restore workflow | basic react-only count checks | still partial; major restore flow remains TODO | Partial |
| `/add` | manual send logging | available | available with current architecture | Complete |
| `/sendrequest` | DM approve/ignore buttons | DM with suggested `/add` only | full DM review + accept/deny + modal reason + send pipeline insert | Complete |
| send request approve/ignore buttons | present | missing | present via Components V2 review sections with button accessories | Complete |
| Dom/me registration | setup DM flow and webhook guidance | simple registration response with URL | new DM setup flow and hidden URL in command response | Partial |
| Sub registration | available | available | available | Complete |
| Throne webhook tracking | tracked webhook events | tracked webhook events | tracked webhook events | Complete |
| leaderboards | top leaderboard formatting | generic summary format | top-10 Dom/me formatting and cleaner totals | Partial |
| backend `robctl` / old `throne` commands | broad command set | reduced set | expanded status/dommes/subs + inactivity + blacklist + leaderboard repair flows | Partial |
| blacklist commands | command family available | backend support | prefix mod commands + robctl blacklist add/remove/list | Complete |
| rule command | legacy text command | not present | not present | Missing |
| DM audit | partial admin auditing in legacy | not ported | not ported | Missing |
| Carl-bot warn handling | integration existed | not present | warn-log listener + courtesy DM relay restored | Complete |
| inactivity removal | periodic warning + kick schedule | not present | bot-state-backed inactivity scheduler + notices + auto-kick + list/test commands | Complete |
| manual send methods | broad method list | reduced methods | reduced methods (parity pending) | Partial |
| UI/cards | legacy style | mixed embeds | true LayoutView/Container/TextDisplay rendering with no embed fallback | Complete |

## Notes

This patch intentionally preserves split webhook/bot services and PostgreSQL-only runtime architecture, and does not reintroduce event-bot/event-window behavior.


## Throne test webhook handling
- Explicit test/setup webhook payloads are detected before send insertion.
- Explicit test events update creator setup verification timestamps (`setup_verified_at`, `last_test_webhook_at`, `last_successful_event_at`) and return `{"ok": true, "setup_verified": true}`.
- Explicit test events do not insert `sends` rows and do not enter the Discord send tracker queue.
- Known test senders can still be stored as real queue items for visible card flow, but are marked `is_test_send=true` and excluded from leaderboards unless test parsing is enabled or the configured owner/test recipient override applies.
- Runtime now uses true LayoutView-based Components V2 rendering when supported, with automatic no embed fallback if required V2 classes are unavailable.

## Old Rob wording / copy reference

- Sources checked:
  - `notpatdev/rob-the-bot` (not accessible from this workspace)
  - `archive/single-process-bot/` (fallback used)
- Copy restored:
  - registration
  - Throne setup
  - errors/snag-paperwork tone
- Copy intentionally changed:
  - copy was centralized into `rob/ui/copy.py` constants/helpers so cogs stop hardcoding long user-facing blocks.


## 2026-05 update
- Added explicit v2 priority calls for inactivity, DM audit, Carl warn relay, and shell helper parity.
- Event runtime remains intentionally not ported unless explicitly requested.

## Current implementation scope
- Added inactivity automation, Carl warn relay, expanded robctl command surface, and moderator blacklist command parity.
- Explicitly excluded by request: DM audit forwarding and `!rule` command.
- Kept architecture guardrails intact: split bot/webhook services, PostgreSQL runtime, no SQLite reintroduction, no legacy single-process bot merge.

- 2026-05-23: Public send card now uses compact Components V2 layout with real `discord.ui.Separator()`, thumbnails, friendly currency names, and purple accent constants from `rob/ui/theme.py`; public send IDs stay out of the public announcement card.
- 2026-05-23: NEW LEADER ALERT posting is now wired live with bot-state dedupe and test-send exclusion.
- 2026-05-23: Leaderboard and stats cards now use explicit separator components, include registered zero-send Dom/mes, and show dynamic maintenance/live status on the main board.
- 2026-05-23: Public send IDs are stored in PostgreSQL and can be backfilled via `robctl sends backfill-public-ids`; public send cards still omit IDs.

- 2026-05-25: `/privacy` now serves a formal, informational-only privacy notice to all users as an ephemeral Components V2 response with no role restrictions.

## Inactivity timing
- New inactive members now use a 7-day no-warning grace period (`INACTIVITY_NEW_MEMBER_GRACE_DAYS`, default `7`).
- First warning sends at ~day 7 and includes Discord relative/full removal timestamps (`<t:...:R> / <t:...:f>`).
- Final warning sends at ~day 14 (`INACTIVITY_FINAL_NOTICE_DAYS` before removal) and clarifies removal is not a ban.
- Removal runs at ~day 21 of inactivity by default.
