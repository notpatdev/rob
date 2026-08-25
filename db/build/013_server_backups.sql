-- Rob v2 DB build script: hourly server-backup snapshots + major-change approvals.
-- Run manually as doadmin in pgAdmin4/psql.
--
-- server_backups stores an hourly JSONB snapshot of a guild's structure (roles,
-- channels, and server settings). server_backup_approvals tracks a pending
-- "major change" that has paused backups until at least N moderators approve it;
-- the approved snapshot then becomes the new baseline backup.
--
-- Run the relevant grants file afterwards so the runtime bot user can use the
-- new tables/sequences (prod_rob_bot / dev_rob_bot grant SELECT/INSERT/UPDATE/
-- DELETE on all tables in schema public, so a re-run of the grants picks these
-- up; ALL TABLES grants already applied also cover them).

BEGIN;

CREATE TABLE IF NOT EXISTS server_backups (
    id BIGSERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    snapshot JSONB NOT NULL,
    is_baseline BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_server_backups_guild_created
ON server_backups (guild_id, created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS server_backup_approvals (
    id BIGSERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    changes JSONB NOT NULL DEFAULT '[]'::jsonb,
    change_signature TEXT,
    pending_snapshot JSONB NOT NULL,
    baseline_backup_id BIGINT REFERENCES server_backups(id) ON DELETE SET NULL,
    required_approvals INTEGER NOT NULL DEFAULT 2,
    approved_by JSONB NOT NULL DEFAULT '[]'::jsonb,
    channel_id BIGINT,
    message_id BIGINT,
    decided_by_user_id BIGINT,
    decision_reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at TIMESTAMPTZ,
    CHECK (status IN ('pending', 'approved', 'rejected', 'superseded'))
);

CREATE INDEX IF NOT EXISTS idx_server_backup_approvals_pending
ON server_backup_approvals (guild_id, status, created_at DESC, id DESC);

INSERT INTO db_build_version (version, notes)
VALUES (
    '013_server_backups',
    'Hourly server-backup snapshots and major-change moderator approvals.'
)
ON CONFLICT (version) DO NOTHING;

COMMIT;
