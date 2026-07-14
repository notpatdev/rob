# Legacy Feature Gap Report (Old Rob vs Rob v2)

This report compares:

- Old bot: `/Users/patfaint/Documents/rob-the-bot-legacy`
- Current v2: `foolishbuilder/rob`

Inspection used direct source reads from old files (not only historical docs), including:

- `bot/event_cog.py`
- `bot/throne_tracker.py`
- `bot/webhook_server.py`
- `bot/event_views.py`
- `bot/ui/components.py`

## Gap Table

| Feature | Old Rob behaviour | v2 status | Should port? | Priority | Notes |
|---|---|---|---|---|---|
| Registration | `/register` modal flow for domme/sub in `bot/event_cog.py` with role/gating logic. | **Complete** | Yes (already) | P0 | v2 split commands `/register domme` and `/register sub`, with runtime DB role checks and Components V2 cards. |
| Throne webhook tracking | Signed webhook ingestion + dedupe + insert + leaderboard sync in `bot/webhook_server.py` and `bot/throne_tracker.py`. | **Complete** | Yes (already) | P0 | v2 webhook server + queue pipeline is in place; no runtime SQLite. |
| Throne page polling / wishlist scraping | Old bot polled Throne pages and wishlist snapshots (`poll_throne_pages`, `sync_wishlist_snapshots`) in `bot/throne_tracker.py`. | **Intentionally not ported** | No | N/A | Current direction is webhook-first pricing and send payloads, not scraping. |
| Public send notifications | Old `SendNotificationView` in `bot/event_views.py` included rank/id/footer details. | **Complete** | Yes (already) | P0 | v2 intentionally keeps public cards cleaner (no public ID/rank/footer internals). |
| Leaderboards | Old bot maintained two channel cards (`event:domme_totals`, `event:sub_leaderboard`) via `sync_leaderboard_channel` in `bot/event_cog.py`. | **Partial** | Needs decision | P1 | v2 uses one main leaderboard + stats card (plus personal `/leaderboard` stats). This is a design shift, not a bug. |
| Counting | Old count restore/failure state machine in `bot/event_cog.py` (`_handle_count_failure`, `process_count_restore_from_send`). | **Partial** | Yes | P0 | v2 now has rescue windows, countdown updates, and send-based restore; non-sub edge-case parity should continue to be validated against old behavior. |
| Send requests | Old `SendRequestDecisionView` in `bot/event_cog.py` with DM approve/ignore buttons. | **Partial** | Yes | P0 | v2 has role restriction, DM review, accept/deny, reason modal, and pipeline insert; continue hardening duplicate-click and DM failure scenarios. |
| Manual sends | Old `/add` in `bot/event_cog.py` logged manual sends through tracker pipeline. | **Complete** | Yes (already) | P0 | v2 `/add` uses normal send pipeline and queue posting. |
| Maintenance | Old maintenance toggle endpoints in `bot/webhook_server.py` (`/admin/maintenance`) and event refresh logic. | **Complete** | Yes (already) | P0 | v2 has DB-backed maintenance state, queue release, and leaderboard status wiring. |
| `robctl` / admin command breadth | Old bot offered larger admin command surface (`!throne status/list/search/addsend/addsub/adddomme`, admin HTTP endpoints). | **Partial** | Yes | P1 | v2 `robctl` now includes legacy-style Throne command coverage (`refresh`, `status`, `list/dommes`, `search`, `webhook refresh`, `addsend`, `addsub`, `adddomme`) plus inactivity toggles, blacklist add/remove/list, leaderboard repair/diagnose, and send maintenance tools. Legacy admin HTTP endpoints remain intentionally unported. |
| Rules helper | Old prefix `!rule` topics in `bot/event_cog.py` (`_RULE_RESPONSES`, `rule` command). | **Missing** | Needs decision | P2 | Not currently present in v2; easy to port as slash or prefix helper if wanted. |
| Reports (Rob issue reporting) | Old bot had event final-report posting, but no user-facing Rob issue `/report` command. | **Complete** | Yes (already) | P1 | v2 now includes `/report` modal + acknowledgement + configured destination routing. |
| DM audit forwarding | Old DM audit relay in `bot/event_cog.py` (`_forward_dm_for_audit`). | **Missing** | Needs decision | P3 | Sensitive/privacy-impacting; should be opt-in with clear policy before port. |
| Carl-bot warn relay | Old warn-log listener + courtesy DM in `bot/event_cog.py` (`_process_carlbot_warn_message`). | **Complete** | Yes (already) | P2 | Restored as a v2 cog listener keyed off `guild_settings.warn_log_channel_id` and `guild_settings.carlbot_user_id`. |
| Blacklist operations | Old `rob-blacklist`, `rob-unblacklist`, `throne-blacklist` in `bot/event_cog.py`. | **Complete** | Yes (already) | P1 | Restored via prefix mod commands plus `robctl blacklist add/remove/list`; global interaction guard remains active. |
| Inactivity removal | Old inactivity loop and removal scheduling in `bot/event_cog.py` (`inactivity_loop`, `_process_inactive_members`). | **Complete** | Yes (already) | P2 | Restored (test guild) as an Active/Inactive role swap driven by message+interaction activity tracking, with the Unverified role parked as inactive, the `inactive_users` countdown table, DM notices, final kick path, and `/inactivelist` + `/inactivitytest`. |
| Server backup + approval | New: hourly snapshot of roles/channels/server settings with a 2-moderator approval gate on major changes. | **Complete** | New feature | P2 | `server_backups` + `server_backup_approvals` tables, hourly cog loop (test guild), `### Major Server Change Detected!` approval prompt pinging `mod_role_id` + `trial_mod_role_id`, and `rob backup status/on/off/run`. |
| Moderation helpers (verification/event-era extras) | Old verification/reaction-role/staff-review flows in `bot/views.py` and `bot/ui/cards.py`. | **Partial** | Needs decision | P3 | Some helpers are outside current v2 scope and may belong in separate moderation tooling. |
| Event runtime / event windows / final event reports | Old event lifecycle + final report system in `bot/event_cog.py` and `bot/event_config.py`. | **Intentionally not ported** | No | N/A | v2 direction explicitly avoids reintroducing legacy event-window runtime unless requested later. |
| UI/card system | Old code used mixed `LayoutView`/container helpers in `bot/ui/components.py` and `bot/event_views.py`. | **Complete** | Yes (already) | P0 | v2 is Components V2-first, separator-based, purple-accent themed, and avoids classic embeds for new cards. |

## Summary

- **Core runtime parity now strong** for webhook sends, queue posting, maintenance state, leaderboard refresh, and registration gating.
- **Remaining intentionally excluded ports by current request** are DM audit forwarding and `!rule`.
- **Intentionally excluded legacy systems** remain excluded by design: event-window runtime/final reports, runtime SQLite coupling, and Throne page scraping as a primary pricing source.
