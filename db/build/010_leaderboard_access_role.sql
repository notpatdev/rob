-- Rob v2 DB build script: leaderboard access role.
-- Run manually as doadmin in pgAdmin4/psql.
--
-- Adds a per-guild role that gates who may view the leaderboard. In the test
-- guild (see rob.config.guilds.TEST_GUILD_ID) holding this role grants use of
-- the /leaderboard command and, via Discord channel permissions configured on
-- the role, read access to the #leaderboard channel. Members opt in through the
-- Dom/me DM setup or the /preferences command, and Rob assigns/removes the role
-- accordingly.
--
-- The column is nullable and inert outside the test guild. No new GRANT is
-- required: the runtime grants on vib_settings cover newly added columns.

BEGIN;

ALTER TABLE vib_settings
    ADD COLUMN IF NOT EXISTS leaderboard_view_role_id BIGINT;

INSERT INTO db_build_version (version, notes)
VALUES (
    '010_leaderboard_access_role',
    'Add vib_settings.leaderboard_view_role_id for test-guild leaderboard access gating.'
)
ON CONFLICT (version) DO NOTHING;

COMMIT;
