# Scheduled wind-down

Rob shuts down in phases. A single flag — `wind_down_phase` (0–3) in the shared
`bot_settings` table — drives everything. Both the bot and the (separate)
webhook process read it, so one flag disables features across both.

- Phase source of truth: `rob/services/wind_down.py`
- Flag accessors: `MaintenanceService.get_wind_down_phase()` / `set_wind_down_phase()` / `wind_down_auto_advance()`
- Scheduler + control: `rob/discord/cogs/wind_down.py` (`WindDownCog`)

## Phases

Boundaries are **08:00 AEST** (a fixed UTC+10 offset — Brisbane, no DST — so the
instants are written directly, no tz database needed). See
`wind_down.PHASE_BOUNDARIES`.

| Phase | Date (2026) | Effect |
| --- | --- | --- |
| 0 | — | Normal operation. |
| 1 | 16 Jul | Sends no longer **posted / leaderboarded** (still recorded), **inactivity** loop off, **warn relay** off, new **registrations** closed. `/add`, backend recording and the count still run. |
| 2 | 20 Jul | Webhook **stops recording** Throne sends, `/add` **closed**. Webhook URLs are functionally invalidated (reversibly) by the recording gate. Only the count runs. |
| 3 | 01 Aug | Everything off, including the **count**. Triggers the **final sequence** (below): final stats post → farewell DMs with keepsake PDFs → anonymisation. |

## How it advances

`WindDownCog.wind_down_loop` (a `discord.ext.tasks` loop, every 15 min) calls
`apply_due_phase()`: it computes the phase due at "now" and, if auto-advance is
on, moves the stored phase **forward only** (monotonic). Every tick re-derives
from the clock, so a restart or a missed tick self-corrects. The loop runs once
immediately on startup, so a fresh deploy lands on the correct phase.

Because features **read** the flag (a "pull" model), nothing is imperatively
"disabled" — flipping the phase is all it takes, and the webhook picks it up on
its next request.

## Manual control — `/winddown` (owner only)

- `/winddown` — show the current phase + auto-advance state.
- `/winddown phase:<0-3>` — force a phase (e.g. to trip early, or to roll back
  during testing while paused).
- `/winddown auto:false` — pause the clock-driven advancement (then set phases
  by hand). `auto:true` resumes it.

## Where each gate lives

| Feature | Phase | Gate |
| --- | --- | --- |
| Send posting | ≥1 | `send_queue_service._post_send` (records, skips the Discord post) |
| Discord leaderboard | ≥1 | `leaderboard_service.refresh_guild` (freezes; public site stays live) |
| Inactivity | ≥1 | `inactivity.py` loop early-return |
| Warn relay | ≥1 | `warn_relay._process_carlbot_warn_message` early-return |
| Registrations | ≥1 | `registration._registrations_blocked_for_guild` |
| Throne recording | ≥2 | `throne/webhooks.py` (acks 200, drops the send) |
| Manual `/add` | ≥2 | `sends.add_send` early-return |
| Counting | ≥3 | `counting_service.process_message` early-return |

The public website / API (`/public/*`) and `/forgetme` deliberately stay up so
people can still retrieve their data during the wind-down.

## The 1 August final sequence (Phase 3)

When the wind-down reaches Phase 3, `WindDownCog` fires
`FinalSequenceService.maybe_run()` (`rob/services/final_sequence.py`) — the last
thing Rob ever does. It runs once, from the wind-down loop tick (and, for
convenience, immediately when the owner forces Phase 3 via `/winddown phase:3`).

Three steps, in a strict order because the last one is **irreversible**:

1. **Final stats** — posts Rob's closing numbers for VIB (`final_stats_card`,
   built from the public `GuildSummary` aggregate) to the leaderboard channel.
   Best-effort: a failure here never blocks the rest.
2. **Farewell DMs** — DMs every registered Dom/me and Sub (the same audience as
   `/shutdown`, via `rob/services/recipients.py`) a thank-you message with their
   personal **keepsake PDF** attached (`generate_sends_pdf`, built from each
   person's own send history). Must run *before* step 3, which erases that
   history. Individual DM failures (closed DMs, blocks) are counted, not fatal.
3. **Anonymise** — `SendsRepository.anonymise_guild_sends` irreversibly strips
   identities (sender name/ids, Discord ids, free-text item fields) from every
   send in the guild, keeping the amount / currency / date and the Dom/me link
   so VIB's public totals still compute.

### Idempotency & resume

Each step sets its own flag in `bot_settings`
(`final_sequence_stats_posted`, `final_sequence_dms_sent`,
`final_sequence_anonymised`, `final_sequence_completed`). A restart part-way
through skips finished steps — the mass DM and the anonymisation never repeat —
and a process-local lock stops two overlapping triggers from running it twice.
Once `final_sequence_completed` is set, `maybe_run()` is a no-op.

The thank-you and stats **wording** lives in `rob/ui/cards/final_sequence.py`
and is owner-editable; the tests assert card structure, not prose.
