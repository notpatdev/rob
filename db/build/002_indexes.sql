-- Rob v2 DB Build Script
-- Apply manually as doadmin after 001_core_schema.sql.

CREATE UNIQUE INDEX IF NOT EXISTS idx_subs_guild_send_name_lower
ON subs (guild_id, lower(send_name));

CREATE INDEX IF NOT EXISTS idx_bot_users_guild_discord_user_id
ON bot_users (guild_id, discord_user_id);

CREATE INDEX IF NOT EXISTS idx_dommes_guild_discord_user_id
ON dommes (guild_id, discord_user_id);

CREATE INDEX IF NOT EXISTS idx_dommes_throne_creator_id
ON dommes (throne_creator_id);

CREATE INDEX IF NOT EXISTS idx_subs_guild_discord_user_id
ON subs (guild_id, discord_user_id);

CREATE INDEX IF NOT EXISTS idx_sends_guild_sent_at
ON sends (guild_id, sent_at DESC);

CREATE INDEX IF NOT EXISTS idx_sends_domme_user_id
ON sends (domme_user_id);

CREATE INDEX IF NOT EXISTS idx_sends_sub_user_id
ON sends (sub_user_id);

CREATE INDEX IF NOT EXISTS idx_sends_event_id
ON sends (event_id);

CREATE INDEX IF NOT EXISTS idx_sends_public_send_id
ON sends (public_send_id);

CREATE INDEX IF NOT EXISTS idx_inactive_users_guild_discord_user_id
ON inactive_users (guild_id, discord_user_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sends_event_id_unique
ON sends (event_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sends_public_send_id_unique
ON sends (public_send_id);

INSERT INTO db_build_version (version, notes)
VALUES ('002_indexes', 'Rob v2 indexes and uniqueness helpers')
ON CONFLICT (version) DO NOTHING;
