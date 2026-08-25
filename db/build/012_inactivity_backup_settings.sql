-- Rob v2 DB build script: activity / inactive-role + server-backup settings.
-- Run manually as doadmin in pgAdmin4/psql.
--
-- Adds per-guild configuration used by two new systems (test guild for now):
--   * active_role_id          - role held by verified, recently-active members.
--   * unverified_role_id       - role marking members who have not yet verified
--                                (parked as inactive, never auto-kicked).
--   * trial_mod_role_id        - trial-moderator role, pinged alongside the
--                                moderator role on server-backup approvals and
--                                accepted as an approver of them.
--   * backup_approval_channel_id - channel where the hourly server-backup system
--                                posts major-change approval prompts.
--
-- All columns are nullable and inert until configured (via `rob guild scan` /
-- `rob guild set-role` / `rob guild set-channel`). No new GRANT is required: the
-- runtime grants on vib_settings cover newly added columns.

BEGIN;

ALTER TABLE vib_settings
    ADD COLUMN IF NOT EXISTS active_role_id BIGINT,
    ADD COLUMN IF NOT EXISTS unverified_role_id BIGINT,
    ADD COLUMN IF NOT EXISTS trial_mod_role_id BIGINT,
    ADD COLUMN IF NOT EXISTS backup_approval_channel_id BIGINT;

INSERT INTO db_build_version (version, notes)
VALUES (
    '012_inactivity_backup_settings',
    'Add vib_settings active/unverified/trial-mod roles + backup approval channel.'
)
ON CONFLICT (version) DO NOTHING;

COMMIT;
