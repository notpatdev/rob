-- Profile accent colour and durable wizard resume position.
--
-- Strictly additive over 0001-0003: only new nullable columns via `ALTER
-- TABLE ... ADD COLUMN` (SQLite/D1 rewrites no existing row data for
-- this), exactly like 0003's `sends.sender_discord_user_id` and
-- `domme_registrations.profile_managed` columns. Nothing in 0001, 0002, or
-- 0003 is dropped, renamed, or otherwise altered, and this file itself must
-- never be edited once shipped -- any further schema change belongs in a
-- later numbered migration.
PRAGMA foreign_keys = ON;

-- A document's optional accent colour, shown on its published profile card.
-- Stored as a plain sRGB integer (0x000000-0xFFFFFF) rather than a hex
-- string so range validation is a trivial numeric CHECK and every reader
-- gets the same representation the bot's preset swatches use. NULL means
-- "no colour" (a neutral card), which is itself a valid, deliberate choice
-- -- not merely "unset" -- exactly like `bio`/`dm_status` already work on
-- this table. On a `linked` server overlay document, this column is
-- inherited from the global document unless the existing sparse
-- `profile_document_overrides` mechanism (0002) carries a
-- `field_name = 'profile_color'` row for it; that row, not this column's
-- nullability, is what distinguishes "inherit" from "deliberately cleared
-- to no colour" for an overlay (see resolver.ts). Bot-facing custom hex
-- entry is validated client-side; this CHECK is the Worker's own storage
-- guarantee regardless of what a caller sends.
ALTER TABLE profile_documents
  ADD COLUMN profile_color INTEGER
  CHECK (profile_color IS NULL OR (profile_color >= 0 AND profile_color <= 16777215));

-- Durable, revision-bound bookmark of exactly which screen of the private
-- wizard a draft was last showing, so a Discord message rebuilt after a bot
-- restart (or a second device) can resume on the same micro-screen instead
-- of only the coarse `current_step`. Both columns are plain nullable TEXT,
-- not CHECK-constrained against a fixed vocabulary, so the bot's evolving
-- stage/substep vocabulary (enforced by the Worker's typed contracts, see
-- `contracts.ts`'s `WIZARD_STAGES`) never forces a schema migration --
-- exactly the same reasoning 0002 already documents for
-- `profile_document_selections.value`. Every draft that already exists at
-- the moment this migration runs gets NULL for both columns; a NULL
-- `wizard_stage` is itself meaningful ("no bookmark recorded yet") and lets
-- the bot deterministically fall back to deriving a stage from the
-- existing `current_step`/document state, so old active drafts keep
-- resuming correctly without this migration having to backfill a guess.
ALTER TABLE profile_drafts ADD COLUMN wizard_stage TEXT;
ALTER TABLE profile_drafts ADD COLUMN wizard_substep TEXT;

-- Staged, unconfirmed Throne identity for the wizard's "is this you?" screen.
--
-- Connecting Throne is a *confirmed* flow, not a success-shaped attachment:
-- the Worker resolves the owner's submitted username/URL, shows them the
-- handle it found, and only creates the `throne_creators` row and issues the
-- one-time webhook secret after they confirm that handle. Those columns are
-- what make "resolved but not yet confirmed" a durable state rather than
-- something held in bot memory: nothing about the creator exists in 0001's
-- `throne_creators` table until confirmation, so an abandoned or mistyped
-- resolution can never leave behind a live webhook route, a secret, or a
-- creator row somebody else's profile would then collide with.
--
-- Only the *hash* of the confirmation capability is stored, exactly like
-- `throne_creators.route_secret_hash` (0001): the plaintext token is
-- returned once, to the resolving request, and a database dump therefore
-- never yields a usable one. `pending_throne_expires_at` keeps the staged
-- identity short-lived so a stale confirmation cannot be replayed days
-- later against a handle that has since changed hands. All five columns are
-- nullable and cleared together the moment a confirmation succeeds (or the
-- draft is restarted), so a draft with `pending_throne_token_hash IS NULL`
-- simply has nothing awaiting confirmation -- which is exactly the state
-- every draft that already exists when this migration runs is left in.
ALTER TABLE profile_drafts ADD COLUMN pending_throne_token_hash TEXT;
ALTER TABLE profile_drafts ADD COLUMN pending_throne_public_creator_id TEXT;
ALTER TABLE profile_drafts ADD COLUMN pending_throne_handle TEXT;
ALTER TABLE profile_drafts ADD COLUMN pending_throne_profile_url TEXT;
ALTER TABLE profile_drafts ADD COLUMN pending_throne_expires_at TEXT;
