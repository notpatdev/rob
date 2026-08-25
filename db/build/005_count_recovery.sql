-- Rob v2 counting recovery and temporary count blocks.
-- Run manually as doadmin in pgAdmin4/psql.

CREATE TABLE IF NOT EXISTS count_recovery_windows (
    id BIGSERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    failed_user_id BIGINT NOT NULL,
    failed_user_role TEXT NOT NULL,
    required_domme_user_id BIGINT,
    required_domme_id BIGINT REFERENCES dommes(id),
    expected_number BIGINT NOT NULL,
    attempted_content TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    resolved_at TIMESTAMPTZ,
    resolution TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (failed_user_role IN ('domme', 'sub')),
    CHECK (resolution IS NULL OR resolution IN ('recovered', 'expired_reset', 'expired_blocked', 'cancelled'))
);

CREATE INDEX IF NOT EXISTS idx_count_recovery_windows_active
ON count_recovery_windows (guild_id, channel_id, expires_at)
WHERE resolved_at IS NULL;

CREATE TABLE IF NOT EXISTS count_blocks (
    id BIGSERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    discord_user_id BIGINT NOT NULL,
    reason TEXT NOT NULL,
    blocked_until TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (guild_id, discord_user_id)
);

CREATE INDEX IF NOT EXISTS idx_count_blocks_active
ON count_blocks (guild_id, blocked_until);

INSERT INTO db_build_version (version, notes)
VALUES ('005_count_recovery', 'Counting recovery windows and temporary count blocks')
ON CONFLICT (version) DO NOTHING;
