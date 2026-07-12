-- Rob v2 sub send-name aliases.
-- Run manually as doadmin in pgAdmin4/psql.

CREATE TABLE IF NOT EXISTS sub_send_names (
    id BIGSERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    sub_id BIGINT NOT NULL REFERENCES subs(id) ON DELETE CASCADE,
    discord_user_id BIGINT NOT NULL,
    send_name TEXT NOT NULL,
    is_primary BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sub_send_names_guild_send_name_lower
ON sub_send_names (guild_id, lower(send_name));

CREATE INDEX IF NOT EXISTS idx_sub_send_names_guild_user
ON sub_send_names (guild_id, discord_user_id);

CREATE INDEX IF NOT EXISTS idx_sub_send_names_sub_id
ON sub_send_names (sub_id);

INSERT INTO db_build_version (version, notes)
VALUES ('004_sub_send_names', 'Sub send-name aliases for up to three Throne usernames')
ON CONFLICT (version) DO NOTHING;
