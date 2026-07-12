-- Rob v2 DB Build Script
-- Apply manually as doadmin in the target database.

CREATE TABLE IF NOT EXISTS db_build_version (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    notes TEXT
);

CREATE TABLE IF NOT EXISTS bot_settings (
    key TEXT PRIMARY KEY,
    value JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by BIGINT
);

CREATE TABLE IF NOT EXISTS bot_users (
    id BIGSERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    discord_user_id BIGINT NOT NULL,
    discord_username TEXT,
    discord_display_name TEXT,
    status TEXT NOT NULL DEFAULT 'allowed',
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (guild_id, discord_user_id),
    CHECK (status IN ('allowed', 'blocked'))
);

CREATE TABLE IF NOT EXISTS dommes (
    id BIGSERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    bot_user_id BIGINT REFERENCES bot_users(id),
    discord_user_id BIGINT NOT NULL,
    throne_url TEXT,
    throne_handle TEXT,
    throne_creator_id TEXT,
    tracking_status TEXT NOT NULL DEFAULT 'active',
    profile_status TEXT NOT NULL DEFAULT 'active',
    hide_own_purchases BOOLEAN,
    webhook_secret TEXT,
    webhook_secret_hash TEXT,
    webhook_connected_at TIMESTAMPTZ,
    overlay_detected BOOLEAN NOT NULL DEFAULT false,
    last_overlay_check_at TIMESTAMPTZ,
    last_successful_event_at TIMESTAMPTZ,
    public_display_name TEXT,
    public_display_name_updated_at TIMESTAMPTZ,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (guild_id, discord_user_id),
    CHECK (tracking_status IN ('active', 'disabled')),
    CHECK (profile_status IN ('active', 'pending_removal', 'disabled'))
);

CREATE TABLE IF NOT EXISTS subs (
    id BIGSERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    bot_user_id BIGINT REFERENCES bot_users(id),
    discord_user_id BIGINT NOT NULL,
    send_name TEXT NOT NULL,
    profile_status TEXT NOT NULL DEFAULT 'active',
    registered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (guild_id, discord_user_id),
    CHECK (profile_status IN ('active', 'pending_removal', 'disabled'))
);

CREATE TABLE IF NOT EXISTS sends (
    id BIGSERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    domme_id BIGINT REFERENCES dommes(id),
    domme_user_id BIGINT NOT NULL,
    sub_id BIGINT REFERENCES subs(id),
    sub_user_id BIGINT,
    sub_name TEXT,
    amount_cents INTEGER NOT NULL DEFAULT 0,
    currency TEXT NOT NULL DEFAULT 'USD',
    original_amount_cents INTEGER,
    original_currency TEXT,
    method TEXT,
    source TEXT NOT NULL DEFAULT 'unknown',
    item_name TEXT,
    item_image_url TEXT,
    logged_by BIGINT,
    external_id TEXT,
    event_id TEXT,
    fallback_event_hash TEXT,
    public_send_id TEXT,
    is_private BOOLEAN NOT NULL DEFAULT false,
    is_test_send BOOLEAN NOT NULL DEFAULT false,
    seeded BOOLEAN NOT NULL DEFAULT false,
    sent_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    discord_post_status TEXT NOT NULL DEFAULT 'pending',
    discord_posted_at TIMESTAMPTZ,
    discord_message_id BIGINT,
    discord_post_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (amount_cents >= 0)
);

CREATE TABLE IF NOT EXISTS vib_settings (
    guild_id BIGINT PRIMARY KEY,
    registration_channel_id BIGINT,
    leaderboard_channel_id BIGINT,
    send_track_channel_id BIGINT,
    counting_channel_id BIGINT,
    report_channel_id BIGINT,
    warn_log_channel_id BIGINT,
    domme_role_id BIGINT,
    sub_role_id BIGINT,
    mod_role_id BIGINT,
    inactive_role_id BIGINT,
    carlbot_user_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS vib_leaderboard (
    id BIGSERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    leaderboard_key TEXT NOT NULL,
    leaderboard_type TEXT NOT NULL DEFAULT 'discord',
    title TEXT NOT NULL DEFAULT 'Send Leaderboard',
    channel_id BIGINT,
    message_id BIGINT,
    public_token TEXT,
    public_enabled BOOLEAN NOT NULL DEFAULT false,
    public_theme TEXT NOT NULL DEFAULT 'default',
    last_refreshed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (guild_id, leaderboard_key),
    CHECK (leaderboard_type IN ('discord', 'public', 'combined'))
);

CREATE TABLE IF NOT EXISTS the_count (
    guild_id BIGINT PRIMARY KEY,
    channel_id BIGINT,
    current_number BIGINT NOT NULL DEFAULT 0,
    last_user_id BIGINT,
    is_enabled BOOLEAN NOT NULL DEFAULT false,
    pending_restore BOOLEAN NOT NULL DEFAULT false,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS inactive_users (
    id BIGSERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    bot_user_id BIGINT REFERENCES bot_users(id),
    discord_user_id BIGINT NOT NULL,
    inactive_role_assigned_at TIMESTAMPTZ,
    remove_at TIMESTAMPTZ,
    initial_notice_sent BOOLEAN NOT NULL DEFAULT false,
    final_notice_sent BOOLEAN NOT NULL DEFAULT false,
    status TEXT NOT NULL DEFAULT 'watching',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (guild_id, discord_user_id),
    CHECK (status IN ('watching', 'notice_sent', 'final_notice_sent', 'resolved', 'ignored'))
);

INSERT INTO db_build_version (version, notes)
VALUES ('001_core_schema', 'Initial Rob v2 core schema')
ON CONFLICT (version) DO NOTHING;
