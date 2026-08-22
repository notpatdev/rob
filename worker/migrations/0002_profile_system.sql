-- Bill profile system: private, versioned identity documents plus the
-- global/server "roots" that point at the currently published one.
--
-- This migration is strictly additive: it only creates new tables/indexes
-- and never touches 0001's `guilds`, Throne, `sends`, or `notifications`
-- tables. It must be safe to apply on top of an already-populated 0001
-- database without altering or losing a single existing row.
--
-- Immutable document model
-- ------------------------
-- `profile_documents` rows are never edited in place once `state` leaves
-- `draft`. Publishing flips a document's `state` from `draft` to
-- `published`; a later edit clones the currently-published document into a
-- brand new `draft` row, and republishing flips the *previous* published
-- document to `superseded`. This gives every user a durable, reviewable
-- publication history and means nothing ever reads a half-written document:
-- a document is either still being drafted (and only visible to its owner)
-- or fully complete and public.
PRAGMA foreign_keys = ON;

CREATE TABLE profile_documents (
  id TEXT PRIMARY KEY,
  owner_user_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('draft', 'published', 'superseded')),
  orientation TEXT CHECK (orientation IN ('domme', 'submissive', 'switch_domme', 'switch_submissive')),
  dm_status TEXT CHECK (dm_status IN ('open', 'by_request', 'after_tribute', 'closed')),
  bio TEXT CHECK (bio IS NULL OR length(bio) <= 300),
  public_send_stats INTEGER NOT NULL DEFAULT 0 CHECK (public_send_stats IN (0, 1)),
  -- Nullable, owner-only association; no FK to keep 0002 self-contained
  -- against 0001's `throne_creators` table without reordering either
  -- migration. Ownership of the referenced creator is re-checked in the
  -- Worker whenever this field is written or read.
  throne_creator_id TEXT,
  -- References a row in `profile_links` for this same document (or, for a
  -- linked server overlay, a currently-visible inherited global link); also
  -- left unenforced by FK since the preferred link may live on a different
  -- document than the one being resolved (see profile_link_visibility).
  preferred_payment_link_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX idx_profile_documents_owner ON profile_documents (owner_user_id);
CREATE INDEX idx_profile_documents_owner_state ON profile_documents (owner_user_id, state);

-- Pronoun / honourific / submissive-label multi-selects. `category`
-- distinguishes the fixed value sets; the exact allowed `value` strings are
-- enforced by the Worker's typed contracts, not by a CHECK here, so new
-- values within an existing category never require a schema migration.
CREATE TABLE profile_document_selections (
  document_id TEXT NOT NULL REFERENCES profile_documents (id) ON DELETE CASCADE,
  category TEXT NOT NULL CHECK (category IN ('pronoun', 'honourific', 'submissive_label')),
  value TEXT NOT NULL,
  sort_order INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (document_id, category, value)
);

CREATE INDEX idx_profile_document_selections_document ON profile_document_selections (document_id);

-- At most three per document; enforced by the Worker at write time (a
-- CHECK/trigger cannot count sibling rows in SQLite).
CREATE TABLE profile_aliases (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES profile_documents (id) ON DELETE CASCADE,
  display_alias TEXT NOT NULL,
  normalized_alias TEXT NOT NULL,
  sort_order INTEGER NOT NULL DEFAULT 0,
  UNIQUE (document_id, normalized_alias)
);

CREATE INDEX idx_profile_aliases_document ON profile_aliases (document_id);

-- At most twelve per document; social vs payment classification drives
-- which links a viewer's "Socials"/"Payment Links" controls surface.
CREATE TABLE profile_links (
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL REFERENCES profile_documents (id) ON DELETE CASCADE,
  platform TEXT NOT NULL,
  public_label TEXT NOT NULL,
  username TEXT,
  normalized_url TEXT NOT NULL,
  link_type TEXT NOT NULL CHECK (link_type IN ('social', 'payment')),
  sort_order INTEGER NOT NULL DEFAULT 0,
  enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX idx_profile_links_document ON profile_links (document_id);

-- Presence of a (document_id, field_name) row on a *linked overlay*
-- document means that field was deliberately set (even to an explicit empty
-- value) and must win over global inheritance during resolution. Absence
-- means "inherit the global document's value for this field" -- this is
-- why overrides are tracked separately from the field values themselves.
CREATE TABLE profile_document_overrides (
  document_id TEXT NOT NULL REFERENCES profile_documents (id) ON DELETE CASCADE,
  field_name TEXT NOT NULL,
  PRIMARY KEY (document_id, field_name)
);

-- Lets a linked server overlay hide a specific inherited global link
-- without copying/mutating the global document. Absence of a row means the
-- inherited link is visible (the default); a row with visible=0 hides it.
CREATE TABLE profile_link_visibility (
  document_id TEXT NOT NULL REFERENCES profile_documents (id) ON DELETE CASCADE,
  inherited_link_id TEXT NOT NULL REFERENCES profile_links (id) ON DELETE CASCADE,
  visible INTEGER NOT NULL DEFAULT 1 CHECK (visible IN (0, 1)),
  PRIMARY KEY (document_id, inherited_link_id)
);

-- One row per user: the "root" pointer for their global identity. Publish
-- bumps `version` and repoints `current_document_id`; see the Worker's
-- publish path for how this row doubles as the optimistic-concurrency guard.
CREATE TABLE global_profiles (
  owner_user_id TEXT PRIMARY KEY,
  current_document_id TEXT NOT NULL REFERENCES profile_documents (id),
  version INTEGER NOT NULL DEFAULT 1,
  published_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

-- One row per (guild, user): the "root" pointer for a server-scoped
-- profile. `mode = 'linked'` documents are sparse overlays resolved against
-- the live global document at read time; `mode = 'independent'` documents
-- are complete and resolved on their own.
CREATE TABLE server_profiles (
  id TEXT PRIMARY KEY,
  guild_id TEXT NOT NULL,
  owner_user_id TEXT NOT NULL,
  mode TEXT NOT NULL CHECK (mode IN ('linked', 'independent')),
  current_document_id TEXT NOT NULL REFERENCES profile_documents (id),
  version INTEGER NOT NULL DEFAULT 1,
  published_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (guild_id, owner_user_id)
);

CREATE INDEX idx_server_profiles_guild ON server_profiles (guild_id);
CREATE INDEX idx_server_profiles_owner ON server_profiles (owner_user_id);

-- Immutable publish history, one row per successful publication. Never
-- updated or deleted; used for audit/debugging and future rollback tooling.
CREATE TABLE profile_publications (
  id TEXT PRIMARY KEY,
  profile_kind TEXT NOT NULL CHECK (profile_kind IN ('global', 'server')),
  owner_user_id TEXT NOT NULL,
  guild_id TEXT,
  version INTEGER NOT NULL,
  document_id TEXT NOT NULL REFERENCES profile_documents (id),
  published_at TEXT NOT NULL
);

CREATE INDEX idx_profile_publications_lookup
  ON profile_publications (profile_kind, owner_user_id, guild_id, version);

-- A document is published exactly once in its lifetime (draft -> published
-- -> eventually superseded); this uniqueness constraint is also the safety
-- net that turns two truly concurrent publish attempts for the same draft
-- into a hard, atomic failure for the loser (a real constraint violation
-- aborts its whole batch) instead of a silently-duplicated history row.
CREATE UNIQUE INDEX idx_profile_publications_document
  ON profile_publications (document_id);

-- Worker-owned wizard state. `base_version` records the root `version` this
-- draft was started/last-restarted against (0 when there is no published
-- document yet), so publish can detect that the root moved underneath the
-- draft even if the draft's own `revision` looks fine. `revision` is bumped
-- on every mutation and must be echoed back by the caller as
-- `expected_revision`; a mismatch means someone/something else already
-- mutated the draft and the caller is working from stale state.
CREATE TABLE profile_drafts (
  id TEXT PRIMARY KEY,
  owner_user_id TEXT NOT NULL,
  origin_guild_id TEXT,
  target_scope TEXT NOT NULL CHECK (target_scope IN ('global', 'server')),
  guild_id TEXT,
  server_mode TEXT CHECK (server_mode IN ('linked', 'independent')),
  document_id TEXT NOT NULL REFERENCES profile_documents (id),
  base_version INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'published')),
  current_step TEXT NOT NULL DEFAULT 'orientation',
  revision INTEGER NOT NULL DEFAULT 0,
  intro_message_id TEXT,
  wizard_message_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  published_at TEXT
);

-- Exactly one active global draft per user...
CREATE UNIQUE INDEX idx_profile_drafts_active_global
  ON profile_drafts (owner_user_id)
  WHERE target_scope = 'global' AND status = 'active';

-- ...and exactly one active server draft per guild/user. Both are partial
-- unique indexes (SQLite/D1-supported) rather than a table-wide uniqueness
-- constraint, because completed/published drafts are kept for history and
-- must not block starting a new one.
CREATE UNIQUE INDEX idx_profile_drafts_active_server
  ON profile_drafts (guild_id, owner_user_id)
  WHERE target_scope = 'server' AND status = 'active';

CREATE INDEX idx_profile_drafts_owner ON profile_drafts (owner_user_id);

-- Collapsed/completed wizard step tracker, keyed by the fixed step_key
-- sequence in the Worker's contracts module.
CREATE TABLE profile_draft_steps (
  draft_id TEXT NOT NULL REFERENCES profile_drafts (id) ON DELETE CASCADE,
  step_key TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'completed')),
  completed_at TEXT,
  PRIMARY KEY (draft_id, step_key)
);
