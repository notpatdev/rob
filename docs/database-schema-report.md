# Rob — Database Schema Report

> A reference for building a website (e.g. in Lovable) on top of Rob's database.
> Generated from the SQL build scripts in `db/build/` and the data-access code in
> `rob/database/`.

---

## 1. What this system is

**Rob** is a Discord bot for a community that tracks money "sends" (tips / gifts
sent through the **Throne** platform) and displays **leaderboards**. It runs as
**two services that share one PostgreSQL database**:

| Service | Role | DB access |
|---|---|---|
| `apps/webhook` | Receives Throne webhooks and writes each incoming send | Narrow (mostly reads + writes `sends`) |
| `apps/bot` | Discord bot: posts sends, refreshes leaderboards, handles commands, counting game, inactivity, backups | Full read/write on all tables |

For a **website**, the interesting data is the money **sends**, the **dommes**
(recipients) and **subs** (senders), and the **leaderboards**. The rest of the
tables (counting game, server backups, onboarding flow, moderation) exist to run
the Discord bot and are usually not needed for a public site — see
§7 "What a website actually needs".

---

## 2. Database technology & conventions

- **Engine:** PostgreSQL. Accessed from Python via **asyncpg** (connection pool,
  see `rob/database/connection.py`). No ORM — raw SQL in
  `rob/database/repositories/`.
- **Schema:** everything lives in the `public` schema.
- **Migrations:** applied **manually** as numbered SQL files in `db/build/`
  (`001_…` → `013_…`). There is no automatic migration tool. A `db_build_version`
  table records which scripts have been applied.
- **No hard `ORM` relations enforced everywhere** — some links are real foreign
  keys, others are "soft" joins on `(guild_id, discord_user_id)`. See §5.

### Conventions you must know before building on this

| Convention | Detail | Why it matters for a website |
|---|---|---|
| **Multi-tenant by `guild_id`** | Almost every table has a `guild_id BIGINT` (the Discord server ID). Data from different servers lives in the same tables. | **Always filter by `guild_id`.** Never show one server's data on another's page. |
| **Money is integer cents** | `amount_cents INTEGER` holds cents, `currency TEXT` (default `'USD'`). There is **no** decimal/float money column. | Display `amount_cents / 100` formatted for `currency`. `2500` = `$25.00`. |
| **Discord IDs are 64-bit** | `guild_id`, `discord_user_id`, channel/role/message IDs are all `BIGINT` (Discord "snowflakes"). | In JavaScript, **treat these as strings**, not numbers — values exceed `Number.MAX_SAFE_INTEGER` (2^53) and lose precision. Have the API return them as strings. |
| **Timestamps are `TIMESTAMPTZ`** | All `*_at` columns are timezone-aware (UTC). | Render in the viewer's local time; the DB is UTC. |
| **Status via text + CHECK** | Enums are modeled as `TEXT` columns with `CHECK (col IN (...))`, not Postgres `ENUM` types. | Valid values are listed per-column below; treat them as fixed enums. |
| **Soft state, not deletes** | Rows carry `status` / `profile_status` / `tracking_status` instead of being deleted. | Filter on status to hide disabled/removed records. |
| **Public IDs exist** | `sends.public_send_id`, `vib_leaderboard.public_token` are opaque public identifiers, distinct from internal `BIGSERIAL` `id`s. | Use these (not raw `id`s) in public URLs. |

---

## 3. Table inventory

18 tables. The "Website relevance" column flags what a public site would use.

| # | Table | Purpose | Website relevance |
|---|---|---|---|
| 1 | `db_build_version` | Which migration scripts have run | — (ops only) |
| 2 | `bot_settings` | Global key/value config (JSONB) | — |
| 3 | `bot_users` | Every Discord user Rob has seen, per guild | Low (identity join) |
| 4 | `dommes` | Registered recipients (Throne creators) | **High** |
| 5 | `subs` | Registered senders + their primary send name | **High** |
| 6 | `sub_send_names` | Extra Throne aliases per sub (up to 3) | Medium |
| 7 | `sends` | **The core money-transaction ledger** | **High** |
| 8 | `vib_settings` | Per-guild channel & role configuration | Low |
| 9 | `vib_leaderboard` | Leaderboard message refs + public-page settings | **High** |
| 10 | `the_count` | State of the "counting" game per guild | — |
| 11 | `inactive_users` | Inactivity tracking / auto-role | — |
| 12 | `count_recovery_windows` | Counting-game recovery state | — |
| 13 | `count_blocks` | Temporary counting bans | — |
| 14 | `send_change_requests` | Approval queue for manual send edits | Low |
| 15 | `domme_onboarding_state` | In-flight DM onboarding flow state | — |
| 16 | `server_backups` | Hourly JSONB snapshots of guild structure | — |
| 17 | `server_backup_approvals` | Moderator approvals for major server changes | — |
| 18 | `user_terms_acceptance` | Terms-of-service acceptance (test guild) | — |

---

## 4. Table-by-table schema

Types are as written in the SQL. `PK` = primary key, `FK` = foreign key,
`U` = part of a unique constraint/index.

### 4.1 `sends` — the money ledger (most important table)

One row per money event received from Throne (or manually logged).

| Column | Type | Notes |
|---|---|---|
| `id` | BIGSERIAL | PK (internal) |
| `guild_id` | BIGINT | NOT NULL. Discord server. |
| `domme_id` | BIGINT | FK → `dommes(id)`, nullable |
| `domme_user_id` | BIGINT | NOT NULL. Recipient's Discord user ID. |
| `sub_id` | BIGINT | FK → `subs(id)`, nullable |
| `sub_user_id` | BIGINT | Nullable — null = "unclaimed" send (no linked sub) |
| `sub_name` | TEXT | Free-text sender name from Throne |
| `amount_cents` | INTEGER | NOT NULL, default 0, `CHECK (amount_cents >= 0)` |
| `currency` | TEXT | NOT NULL, default `'USD'` |
| `original_amount_cents` | INTEGER | Pre-conversion amount, nullable |
| `original_currency` | TEXT | Pre-conversion currency, nullable |
| `method` | TEXT | Payment method, nullable |
| `source` | TEXT | NOT NULL, default `'unknown'` (e.g. throne / manual) |
| `item_name` | TEXT | Wishlist item name, nullable |
| `item_image_url` | TEXT | Item image, nullable |
| `logged_by` | BIGINT | Discord user who manually logged it, nullable |
| `external_id` | TEXT | Throne's external ID, nullable |
| `event_id` | TEXT | Throne event ID — **unique** (dedup key) |
| `fallback_event_hash` | TEXT | Dedup hash when `event_id` is null — **partial unique** |
| `public_send_id` | TEXT | Opaque public ID — **unique**. Use in public URLs. |
| `is_private` | BOOLEAN | NOT NULL, default false. Hide from public totals. |
| `is_test_send` | BOOLEAN | NOT NULL, default false |
| `seeded` | BOOLEAN | NOT NULL, default false (backfilled/imported) |
| `sent_at` | TIMESTAMPTZ | NOT NULL. When the send happened. |
| `received_at` | TIMESTAMPTZ | NOT NULL, default now() |
| `discord_post_status` | TEXT | NOT NULL, default `'pending'` (`pending` → `posted` etc.) |
| `discord_posted_at` | TIMESTAMPTZ | nullable |
| `discord_message_id` | BIGINT | nullable |
| `discord_post_error` | TEXT | nullable |
| `created_at` | TIMESTAMPTZ | NOT NULL, default now() |

**Dedup:** `event_id` is uniquely indexed; when it's null, `fallback_event_hash`
is uniquely indexed (partial, only where `event_id IS NULL`). Throne delivers
at-least-once, so retries are collapsed by these.

**A send only "counts" for leaderboards when** (from
`rob/database/repositories/leaderboards.py`):
`discord_post_status = 'posted'` **AND** `is_private = false` **AND** it is not a
test send (`is_test_send = false` and the sub name isn't a configured test
gifter). Keep this filter for any public totals.

### 4.2 `dommes` — recipients (Throne creators)

One row per registered recipient per guild.

| Column | Type | Notes |
|---|---|---|
| `id` | BIGSERIAL | PK |
| `guild_id` | BIGINT | NOT NULL. `UNIQUE (guild_id, discord_user_id)`. |
| `bot_user_id` | BIGINT | FK → `bot_users(id)`, nullable |
| `discord_user_id` | BIGINT | NOT NULL |
| `throne_url` | TEXT | Their Throne page |
| `throne_handle` | TEXT | Throne username |
| `throne_creator_id` | TEXT | Throne's internal creator ID |
| `tracking_status` | TEXT | default `'active'`, `CHECK IN ('active','disabled')` |
| `profile_status` | TEXT | default `'active'`, `CHECK IN ('active','pending_removal','disabled')` |
| `hide_own_purchases` | BOOLEAN | nullable |
| `webhook_secret` | TEXT | **secret** — never expose |
| `webhook_secret_hash` | TEXT | **secret** — never expose |
| `webhook_connected_at` | TIMESTAMPTZ | nullable |
| `overlay_detected` | BOOLEAN | NOT NULL, default false |
| `last_overlay_check_at` | TIMESTAMPTZ | nullable |
| `last_successful_event_at` | TIMESTAMPTZ | nullable |
| `public_display_name` | TEXT | **Name to show publicly** |
| `public_display_name_updated_at` | TIMESTAMPTZ | nullable |
| `registered_at` | TIMESTAMPTZ | NOT NULL, default now() |
| `created_at` / `updated_at` | TIMESTAMPTZ | default now() |
| `send_notifications_enabled` | BOOLEAN | NOT NULL, default true *(added in 008)* |
| `leaderboard_visible` | BOOLEAN | NOT NULL, default true — **opt-out of public leaderboard** *(008)* |
| `notifications_snoozed_until` | TIMESTAMPTZ | nullable *(008)* |
| `preferences_deferred_until` | TIMESTAMPTZ | nullable *(008)* |
| `preferences_confirmed_at` | TIMESTAMPTZ | nullable *(008)* |

**Public display name resolution** (from the public leaderboard query):
`public_display_name` → else `throne_handle` → else the literal
`'Registered Dom/me'`. Respect `leaderboard_visible` before showing a domme.

### 4.3 `subs` — senders

| Column | Type | Notes |
|---|---|---|
| `id` | BIGSERIAL | PK |
| `guild_id` | BIGINT | NOT NULL. `UNIQUE (guild_id, discord_user_id)`. |
| `bot_user_id` | BIGINT | FK → `bot_users(id)`, nullable |
| `discord_user_id` | BIGINT | NOT NULL |
| `send_name` | TEXT | NOT NULL. Primary Throne name. Unique per guild, case-insensitive (`idx_subs_guild_send_name_lower`). |
| `profile_status` | TEXT | default `'active'`, `CHECK IN ('active','pending_removal','disabled')` |
| `registered_at` / `created_at` / `updated_at` | TIMESTAMPTZ | default now() |

### 4.4 `sub_send_names` — sender aliases

Lets one sub claim up to three Throne usernames.

| Column | Type | Notes |
|---|---|---|
| `id` | BIGSERIAL | PK |
| `guild_id` | BIGINT | NOT NULL |
| `sub_id` | BIGINT | NOT NULL, FK → `subs(id)` **ON DELETE CASCADE** |
| `discord_user_id` | BIGINT | NOT NULL |
| `send_name` | TEXT | NOT NULL. Unique per guild, case-insensitive. |
| `is_primary` | BOOLEAN | NOT NULL, default false |
| `created_at` / `updated_at` | TIMESTAMPTZ | default now() |

### 4.5 `bot_users` — every seen Discord user

| Column | Type | Notes |
|---|---|---|
| `id` | BIGSERIAL | PK |
| `guild_id` | BIGINT | NOT NULL. `UNIQUE (guild_id, discord_user_id)`. |
| `discord_user_id` | BIGINT | NOT NULL |
| `discord_username` | TEXT | nullable |
| `discord_display_name` | TEXT | nullable |
| `status` | TEXT | default `'allowed'`, `CHECK IN ('allowed','blocked')` |
| `first_seen_at` | TIMESTAMPTZ | NOT NULL, default now() |
| `last_seen_at` | TIMESTAMPTZ | nullable |
| `created_at` / `updated_at` | TIMESTAMPTZ | default now() |

### 4.6 `vib_leaderboard` — leaderboard messages & public pages

| Column | Type | Notes |
|---|---|---|
| `id` | BIGSERIAL | PK |
| `guild_id` | BIGINT | NOT NULL. `UNIQUE (guild_id, leaderboard_key)`. |
| `leaderboard_key` | TEXT | NOT NULL — logical name of the board |
| `leaderboard_type` | TEXT | default `'discord'`, `CHECK IN ('discord','public','combined')` |
| `title` | TEXT | NOT NULL, default `'Send Leaderboard'` |
| `channel_id` | BIGINT | nullable |
| `message_id` | BIGINT | nullable |
| `public_token` | TEXT | **opaque token for a public web page** |
| `public_enabled` | BOOLEAN | NOT NULL, default false — is the public page live? |
| `public_theme` | TEXT | NOT NULL, default `'default'` |
| `last_refreshed_at` | TIMESTAMPTZ | nullable |
| `created_at` / `updated_at` | TIMESTAMPTZ | default now() |

> These columns (`public_token`, `public_enabled`, `public_theme`) are exactly
> the hooks for a hosted public leaderboard page. A website can look up a board
> by `public_token`, confirm `public_enabled = true`, then render the standings.

### 4.7 `vib_settings` — per-guild config (one row per guild)

`guild_id` is the PK. Holds Discord channel and role IDs the bot uses. Mostly
irrelevant to a website; included for completeness.

Channels: `registration_channel_id`, `leaderboard_channel_id`,
`send_track_channel_id`, `counting_channel_id`, `report_channel_id`,
`warn_log_channel_id`, `backup_approval_channel_id`.
Roles: `domme_role_id`, `sub_role_id`, `mod_role_id`, `inactive_role_id`,
`leaderboard_view_role_id`, `active_role_id`, `unverified_role_id`,
`trial_mod_role_id`. Plus `carlbot_user_id`, `created_at`, `updated_at`.

### 4.8 `bot_settings` — global key/value

`key TEXT PK`, `value JSONB NOT NULL DEFAULT '{}'`, `updated_at`, `updated_by BIGINT`.
Values are wrapped as `{"value": "..."}` (see `bot_settings.py`). Global, not
per-guild.

### 4.9 Counting-game tables (`the_count`, `count_recovery_windows`, `count_blocks`)

Discord "counting" mini-game. Not relevant to a money/leaderboard website.

- **`the_count`** (`guild_id` PK): `channel_id`, `current_number BIGINT`,
  `last_user_id`, `is_enabled`, `pending_restore`, `updated_at`.
- **`count_recovery_windows`**: recovery flow after a counting mistake;
  `failed_user_role CHECK IN ('domme','sub')`,
  `resolution CHECK IN ('recovered','expired_reset','expired_blocked','cancelled')`.
- **`count_blocks`**: temporary bans; `UNIQUE (guild_id, discord_user_id)`,
  `blocked_until TIMESTAMPTZ`.

### 4.10 `inactive_users` — inactivity tracking

`UNIQUE (guild_id, discord_user_id)`. `status` `CHECK IN ('watching',
'notice_sent','final_notice_sent','resolved','ignored')`. Tracks `remove_at`,
notice flags, `inactive_role_assigned_at`.

### 4.11 `send_change_requests` — manual-edit approval queue

Approval workflow for adding/removing/updating sends via CLI ("Bash rob").
`action CHECK IN ('send_add','send_remove','send_update')`,
`status CHECK IN ('pending','approved','rejected','failed')`. FKs
`target_send_id` / `approved_send_id` → `sends(id) ON DELETE SET NULL`.

### 4.12 `domme_onboarding_state` — DM onboarding flow

Short-lived per-domme flow state. `UNIQUE (guild_id, discord_user_id)`,
`stage CHECK IN ('intro','awaiting_throne_input','awaiting_identity_confirm',
'awaiting_webhook','awaiting_preferences','completed')`.

### 4.13 Server-backup tables (`server_backups`, `server_backup_approvals`)

- **`server_backups`**: hourly JSONB `snapshot` of guild structure (roles,
  channels, settings), `is_baseline BOOLEAN`.
- **`server_backup_approvals`**: pending major-change approvals;
  `status CHECK IN ('pending','approved','rejected','superseded')`,
  JSONB `changes` / `pending_snapshot` / `approved_by`,
  `required_approvals INTEGER default 2`, FK `baseline_backup_id` →
  `server_backups(id) ON DELETE SET NULL`.

### 4.14 `user_terms_acceptance` — ToS (test guild)

`discord_user_id` PK, `status CHECK IN ('pending','accepted','declined')`,
`terms_version`, prompt/accept/decline timestamps. Optional (added in build 009,
test-guild only).

### 4.15 `db_build_version` — migration log

`version TEXT PK`, `applied_at`, `notes`. Ops only.

---

## 5. Relationships

**Hard foreign keys (enforced):**

```
bot_users(id) ─┬─< dommes.bot_user_id
               ├─< subs.bot_user_id
               └─< inactive_users.bot_user_id

dommes(id) ────┬─< sends.domme_id
               └─< count_recovery_windows.required_domme_id

subs(id) ──────┬─< sends.sub_id
               └─< sub_send_names.sub_id            (ON DELETE CASCADE)

sends(id) ─────┬─< send_change_requests.target_send_id   (ON DELETE SET NULL)
               └─< send_change_requests.approved_send_id (ON DELETE SET NULL)

server_backups(id) ─< server_backup_approvals.baseline_backup_id (ON DELETE SET NULL)
```

**Soft links (join keys, not FK-enforced):** most cross-table lookups also/instead
use the pair **`(guild_id, discord_user_id)`**. A send records both `domme_id`
(FK, may be null) **and** `domme_user_id` (raw Discord ID). Leaderboard code
resolves the recipient as
`COALESCE(dommes.discord_user_id via domme_id, sends.domme_user_id)` — i.e. it
prefers the FK link and falls back to the raw user ID. Build website joins the
same way.

**Entity relationship (conceptual):**

```
        guild (Discord server, by guild_id)
          │  (every table below is scoped by guild_id)
          ▼
   ┌──────────────┬───────────────┐
 dommes          subs        bot_users
   │  (recipient)  │ (sender)
   └──────┬────────┘
          ▼
        sends  ── the money ledger (amount_cents, currency, sent_at, status…)
          │
          ▼
   vib_leaderboard  ── aggregates sends into public/Discord standings
```

---

## 6. Indexes & uniqueness (query-planning cheatsheet)

Unique constraints / indexes:
- `bot_users`, `dommes`, `subs`, `inactive_users`, `domme_onboarding_state`,
  `count_blocks`: `UNIQUE (guild_id, discord_user_id)`.
- `subs` & `sub_send_names`: `UNIQUE (guild_id, lower(send_name))` — send names
  are case-insensitively unique per guild.
- `sends`: unique `event_id`; partial unique `fallback_event_hash` where
  `event_id IS NULL`; unique `public_send_id`.
- `vib_settings`: `guild_id` PK. `the_count`: `guild_id` PK.
- `vib_leaderboard`: `UNIQUE (guild_id, leaderboard_key)`.

Performance indexes you can rely on for reads:
- `idx_sends_guild_sent_at` on `sends (guild_id, sent_at DESC)` — recent sends.
- `idx_sends_domme_user_id`, `idx_sends_sub_user_id` — per-person histories.
- `idx_dommes_throne_creator_id`, `idx_dommes_leaderboard_visible (guild_id) WHERE leaderboard_visible`.
- `idx_bot_users_guild_discord_user_id`, `idx_subs_guild_discord_user_id`, etc.

---

## 7. What a website actually needs

For a **public leaderboard / stats site**, you only need a handful of tables:
`sends`, `dommes`, `subs`, `sub_send_names`, and `vib_leaderboard`.

### The canonical "counted send" filter

Every public total must apply this (mirrors
`rob/database/repositories/leaderboards.py`):

```sql
-- A send counts when it is posted, public, and not a test send.
WHERE s.guild_id = $guild_id
  AND s.discord_post_status = 'posted'
  AND s.is_private = false
  AND COALESCE(s.is_test_send, false) = false
  -- (optionally also exclude sends whose sub_name is a configured test gifter)
```

### Recipient resolution (used by the leaderboard)

```sql
-- Prefer the FK-linked domme's user id, fall back to the raw domme_user_id.
COALESCE(d.discord_user_id, s.domme_user_id) AS recipient_user_id
FROM sends s
LEFT JOIN dommes d ON d.id = s.domme_id AND d.guild_id = s.guild_id
```

### Example: top dommes (public page)

```sql
WITH valid_sends AS (
  SELECT s.*, COALESCE(d.discord_user_id, s.domme_user_id) AS recipient_user_id
  FROM sends s
  LEFT JOIN dommes d ON d.id = s.domme_id AND d.guild_id = s.guild_id
  WHERE s.guild_id = $1
    AND s.discord_post_status = 'posted'
    AND s.is_private = false
    AND COALESCE(s.is_test_send, false) = false
)
SELECT
  COALESCE(NULLIF(TRIM(d.public_display_name), ''),
           NULLIF(TRIM(d.throne_handle), ''),
           'Registered Dom/me')      AS display_name,
  COALESCE(SUM(v.amount_cents), 0)   AS total_cents,
  COUNT(v.id)                        AS send_count
FROM dommes d
LEFT JOIN valid_sends v
  ON v.guild_id = d.guild_id AND v.recipient_user_id = d.discord_user_id
WHERE d.guild_id = $1
  AND d.leaderboard_visible = true          -- respect opt-out
GROUP BY d.discord_user_id, d.public_display_name, d.throne_handle
ORDER BY total_cents DESC, send_count DESC
LIMIT 10;
```

Format `total_cents / 100` as currency for display.

### Summary tiles the bot already computes

`total_cents`, `send_count`, `domme_count`, `sub_count`,
`unclaimed_send_count` (sends with `sub_user_id IS NULL`), and
`unclaimed_total_cents`. See `LeaderboardsRepository.get_summary`.

---

## 8. Security & privacy notes (important for a public site)

- **Never expose these columns:** `dommes.webhook_secret`,
  `dommes.webhook_secret_hash`. They authenticate Throne webhooks.
- **Respect visibility flags:** hide sends where `is_private = true`; hide dommes
  where `leaderboard_visible = false`; skip test data (`is_test_send`, seeded).
- **Discord IDs are PII-ish.** For public pages prefer display names
  (`public_display_name` / `throne_handle`) and `public_send_id` /
  `public_token` over raw Discord user IDs.
- **Use a read-only DB role** for the website. The webhook role
  (`prod_rob_webhook`) is already scoped down (no `DELETE`, no `CREATE`); a
  website role should be `SELECT`-only on the handful of tables in §7. Grant
  patterns live in `db/grants/`.
- **64-bit IDs → strings in JSON.** Serialize all `*_id` BIGINT snowflakes as
  strings so JavaScript doesn't corrupt them.

---

## 9. How to (re)build the schema from scratch

Apply these as a Postgres superuser (`doadmin`), in order (from
`db/build/README.md`):

```
001_core_schema.sql          -- core tables
002_indexes.sql              -- indexes + uniqueness
004_sub_send_names.sql       -- sender aliases
005_count_recovery.sql       -- counting recovery + blocks
006_send_change_requests.sql -- manual-edit approval queue
007_send_update_requests.sql -- allow send_update action
008_dm_preferences.sql       -- DM prefs + onboarding (drops old achievements)
009_terms_acceptance.sql     -- ToS table (test guild)
010_leaderboard_access_role.sql
011_send_fallback_hash_unique.sql
012_inactivity_backup_settings.sql
013_server_backups.sql
```

`003_achievements.sql` is retired (dropped by `008`). Then apply the appropriate
file in `db/grants/` so runtime users get privileges. `ops/checks/check_db.py`
verifies the applied versions, required columns, and grants.

---

*Source of truth: `db/build/*.sql` (DDL) and `rob/database/repositories/*.py`
(queries / semantics). If they ever disagree with this document, the SQL wins.*
