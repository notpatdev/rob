-- Bill link management/importer, guild setup wizard, and send attribution.
--
-- Strictly additive over 0001 and 0002: only new tables/indexes plus one
-- nullable column added to the existing `sends` table via `ALTER TABLE ...
-- ADD COLUMN` (SQLite/D1 rewrites no existing row data for this). Nothing
-- in 0001 or 0002 is dropped, renamed, or otherwise altered, and this file
-- itself must never be edited once shipped -- any further schema change
-- belongs in a later numbered migration.
PRAGMA foreign_keys = ON;

-- Attribution: which Discord user (if any) sent the tribute behind a given
-- `sends` row, resolved via the recipient guild's *effective* alias set at
-- webhook-processing time (see `aliasAttribution.ts`). Nullable because
-- private/anonymous events and unmatched/ambiguous senders are never
-- attributed, and because every row inserted before this migration ran has
-- no attribution data at all -- existing sends are never retroactively
-- attributed, only sends recorded from here on can carry this column.
ALTER TABLE sends ADD COLUMN sender_discord_user_id TEXT;

CREATE INDEX idx_sends_guild_sender ON sends (guild_id, sender_discord_user_id);

-- Distinguishes registrations materialized by profile publication from v1
-- registrations created explicitly through the legacy API. Profile-managed
-- rows can be deactivated when a profile disconnects Throne; legacy rows must
-- remain untouched for rollout compatibility.
ALTER TABLE domme_registrations
  ADD COLUMN profile_managed INTEGER NOT NULL DEFAULT 0
  CHECK (profile_managed IN (0, 1));

CREATE INDEX idx_domme_registrations_profile_managed
  ON domme_registrations (guild_id, discord_user_id, profile_managed);

-- One link-page import attempt for a draft's links step: `source_url` is
-- the page the caller asked to import, `provider` records which adapter
-- handled it ("linktree"/"allmylinks"/"beacons"/"generic"), and `status`
-- records the SSRF-defended fetch's outcome so the wizard can explain a
-- failure (and fall back to safe manual entry) without re-fetching.
CREATE TABLE profile_link_imports (
  id TEXT PRIMARY KEY,
  draft_id TEXT NOT NULL REFERENCES profile_drafts (id) ON DELETE CASCADE,
  source_url TEXT NOT NULL,
  provider TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('ready', 'no_links_found', 'fetch_failed', 'blocked')),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX idx_profile_link_imports_draft ON profile_link_imports (draft_id);

-- Candidates extracted from one import, offered to the caller for
-- selection before anything is copied into `profile_links`. This table
-- never holds raw HTML -- only the already-normalized candidate fields --
-- and rows are deleted once their import is confirmed or superseded.
CREATE TABLE profile_link_import_candidates (
  id TEXT PRIMARY KEY,
  import_id TEXT NOT NULL REFERENCES profile_link_imports (id) ON DELETE CASCADE,
  platform TEXT NOT NULL,
  public_label TEXT NOT NULL,
  username TEXT,
  normalized_url TEXT NOT NULL,
  link_type TEXT NOT NULL CHECK (link_type IN ('social', 'payment')),
  sort_order INTEGER NOT NULL DEFAULT 0,
  selected INTEGER NOT NULL DEFAULT 1 CHECK (selected IN (0, 1))
);

CREATE INDEX idx_profile_link_import_candidates_import ON profile_link_import_candidates (import_id);

-- Persistent, revision-checked session backing the public `/bill setup`
-- wizard -- the same optimistic-concurrency shape as `profile_drafts`, just
-- for guild-wide (not per-user) configuration state.
CREATE TABLE guild_setup_sessions (
  id TEXT PRIMARY KEY,
  guild_id TEXT NOT NULL,
  initiator_user_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'completed', 'cancelled', 'expired')),
  current_step TEXT NOT NULL DEFAULT 'channel' CHECK (current_step IN ('channel', 'confirm')),
  selected_channel_id TEXT,
  revision INTEGER NOT NULL DEFAULT 0,
  public_message_id TEXT,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT
);

CREATE INDEX idx_guild_setup_sessions_guild ON guild_setup_sessions (guild_id);

-- One active session per guild at a time (mirrors profile_drafts' partial
-- unique index); completed/cancelled/expired sessions are kept for history
-- and never block starting a new one.
CREATE UNIQUE INDEX idx_guild_setup_sessions_guild_active
  ON guild_setup_sessions (guild_id)
  WHERE status = 'active';
