-- Bill's D1 schema. Discord snowflakes are stored as TEXT decimal strings
-- and must never be coerced to JS/SQLite numbers. Throne-side identifiers
-- (creator IDs, event/order IDs) are opaque TEXT values from Throne's API
-- and are not guaranteed to be decimal strings.

PRAGMA foreign_keys = ON;

CREATE TABLE guilds (
  guild_id TEXT PRIMARY KEY,
  send_channel_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE throne_creators (
  id TEXT PRIMARY KEY,
  public_creator_id TEXT NOT NULL,
  handle TEXT NOT NULL,
  profile_url TEXT NOT NULL,
  route_secret_hash TEXT NOT NULL,
  owner_discord_user_id TEXT NOT NULL,
  webhook_verified_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX idx_throne_creators_public_creator_id
  ON throne_creators (public_creator_id);

CREATE INDEX idx_throne_creators_owner
  ON throne_creators (owner_discord_user_id);

CREATE TABLE domme_registrations (
  id TEXT PRIMARY KEY,
  guild_id TEXT NOT NULL REFERENCES guilds (guild_id),
  creator_id TEXT NOT NULL REFERENCES throne_creators (id),
  discord_user_id TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX idx_domme_registrations_guild_creator
  ON domme_registrations (guild_id, creator_id);

-- One Dom/me (Discord user) may have at most one active creator association
-- per guild; re-registering the same user in the same guild updates their
-- existing row instead of creating a second one.
CREATE UNIQUE INDEX idx_domme_registrations_guild_user
  ON domme_registrations (guild_id, discord_user_id);

CREATE INDEX idx_domme_registrations_creator_active
  ON domme_registrations (creator_id, active);

CREATE TABLE throne_events (
  id TEXT PRIMARY KEY,
  creator_id TEXT NOT NULL REFERENCES throne_creators (id),
  raw_type TEXT NOT NULL,
  normalized_type TEXT NOT NULL,
  event_id TEXT,
  order_id TEXT,
  fallback_hash TEXT,
  amount_minor INTEGER NOT NULL DEFAULT 0,
  currency TEXT NOT NULL DEFAULT 'USD',
  sender_username TEXT,
  sender_display_name TEXT,
  item_name TEXT,
  item_image_url TEXT,
  is_private INTEGER NOT NULL DEFAULT 0,
  is_anonymous INTEGER NOT NULL DEFAULT 0,
  purchased_at TEXT,
  received_at TEXT NOT NULL
);

-- Dedup: an event carrying a real event id is unique per creator.
CREATE UNIQUE INDEX idx_throne_events_creator_event_id
  ON throne_events (creator_id, event_id)
  WHERE event_id IS NOT NULL;

-- Dedup: an event carrying an order id is unique per creator.
CREATE UNIQUE INDEX idx_throne_events_creator_order_id
  ON throne_events (creator_id, order_id)
  WHERE order_id IS NOT NULL;

-- Dedup fallback for payloads with neither id: stable content hash.
CREATE UNIQUE INDEX idx_throne_events_creator_fallback_hash
  ON throne_events (creator_id, fallback_hash)
  WHERE fallback_hash IS NOT NULL;

CREATE INDEX idx_throne_events_creator_received
  ON throne_events (creator_id, received_at);

CREATE TABLE sends (
  id TEXT PRIMARY KEY,
  event_id TEXT NOT NULL REFERENCES throne_events (id),
  guild_id TEXT NOT NULL REFERENCES guilds (guild_id),
  registration_id TEXT NOT NULL REFERENCES domme_registrations (id),
  created_at TEXT NOT NULL
);

CREATE UNIQUE INDEX idx_sends_event_guild
  ON sends (event_id, guild_id);

CREATE INDEX idx_sends_guild
  ON sends (guild_id);

CREATE TABLE notifications (
  id TEXT PRIMARY KEY,
  send_id TEXT NOT NULL REFERENCES sends (id),
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 5,
  next_attempt_at TEXT NOT NULL,
  lease_token TEXT,
  lease_owner TEXT,
  lease_expires_at TEXT,
  message_id TEXT,
  last_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX idx_notifications_send_id
  ON notifications (send_id);

CREATE INDEX idx_notifications_status_next_attempt
  ON notifications (status, next_attempt_at);

CREATE INDEX idx_notifications_lease_expires
  ON notifications (status, lease_expires_at);
