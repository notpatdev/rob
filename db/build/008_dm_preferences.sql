-- Rob v2 DB build script: DM-based notification preferences and onboarding.
-- Run manually as doadmin in pgAdmin4/psql.
--
-- Two things happen here:
--
-- A. Remove old achievements-related database objects.
--    The achievements system is no longer used. We drop both tables, their
--    indexes (implicit), and explicit sequences. The originating build entry
--    ``003_achievements`` is also removed from db_build_version so check_db
--    no longer expects the achievements schema to be present.
--
-- B. Add new DB fields for the DM notification system.
--    - Per-Dom/me preference columns on ``dommes``.
--    - A separate ``domme_onboarding_state`` table for in-flight DM setup.
--      This keeps short-lived flow state (DM message ids, pending throne
--      input, current step) off the canonical ``dommes`` row.

BEGIN;

-- ---------------------------------------------------------------------------
-- A. Drop old achievements objects.
-- ---------------------------------------------------------------------------

DROP TABLE IF EXISTS achievement_events;
DROP TABLE IF EXISTS user_achievements;

DROP SEQUENCE IF EXISTS achievement_events_id_seq;
DROP SEQUENCE IF EXISTS user_achievements_id_seq;

-- Forget the prior achievements build entry so check_db does not re-require it.
DELETE FROM db_build_version WHERE version = '003_achievements';

-- ---------------------------------------------------------------------------
-- B. DM notification + onboarding preferences on dommes.
-- ---------------------------------------------------------------------------

ALTER TABLE dommes
    ADD COLUMN IF NOT EXISTS send_notifications_enabled BOOLEAN NOT NULL DEFAULT true,
    ADD COLUMN IF NOT EXISTS leaderboard_visible        BOOLEAN NOT NULL DEFAULT true,
    ADD COLUMN IF NOT EXISTS notifications_snoozed_until TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS preferences_deferred_until  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS preferences_confirmed_at    TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_dommes_leaderboard_visible
    ON dommes (guild_id) WHERE leaderboard_visible = true;

-- ---------------------------------------------------------------------------
-- B (cont). Onboarding flow state.
-- ---------------------------------------------------------------------------
--
-- One row per Dom/me in the DM onboarding flow. The same DM message gets
-- edited as the user progresses (intro → identity confirm → webhook wait →
-- preferences → success); we persist channel/message ids so the bot can
-- recover the message after a restart or webhook callback.

CREATE TABLE IF NOT EXISTS domme_onboarding_state (
    id BIGSERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    discord_user_id BIGINT NOT NULL,
    stage TEXT NOT NULL DEFAULT 'intro',
    pending_throne_input TEXT,
    pending_throne_handle TEXT,
    pending_throne_creator_id TEXT,
    dm_channel_id BIGINT,
    dm_message_id BIGINT,
    last_interaction_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (guild_id, discord_user_id),
    CHECK (stage IN (
        'intro',
        'awaiting_throne_input',
        'awaiting_identity_confirm',
        'awaiting_webhook',
        'awaiting_preferences',
        'completed'
    ))
);

CREATE INDEX IF NOT EXISTS idx_domme_onboarding_state_guild_user
    ON domme_onboarding_state (guild_id, discord_user_id);

-- ---------------------------------------------------------------------------
-- Record the build version.
-- ---------------------------------------------------------------------------

INSERT INTO db_build_version (version, notes)
VALUES (
    '008_dm_preferences',
    'Drop achievements; add DM notification preferences and domme_onboarding_state.'
)
ON CONFLICT (version) DO NOTHING;

COMMIT;
