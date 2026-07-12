-- Rob v2 DB Build Script
-- Apply manually as doadmin in the target database.

CREATE TABLE IF NOT EXISTS user_terms_acceptance (
    discord_user_id BIGINT PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'pending',
    terms_version TEXT NOT NULL,
    dm_channel_id BIGINT,
    dm_message_id BIGINT,
    first_prompted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_prompted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    accepted_at TIMESTAMPTZ,
    declined_at TIMESTAMPTZ,
    CHECK (status IN ('pending', 'accepted', 'declined'))
);

INSERT INTO db_build_version (version, notes)
VALUES (
    '009_terms_acceptance',
    'Add test-guild user Terms acceptance tracking.'
)
ON CONFLICT (version) DO NOTHING;
