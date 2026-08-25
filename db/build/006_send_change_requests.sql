-- Rob v2 backend send-change approval requests.
-- Run manually as doadmin in pgAdmin4/psql.

CREATE TABLE IF NOT EXISTS send_change_requests (
    id BIGSERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    domme_user_id BIGINT NOT NULL,
    action TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    requested_by TEXT NOT NULL,
    requested_sub_name TEXT,
    amount_cents INTEGER,
    currency TEXT,
    method TEXT,
    note TEXT,
    target_send_id BIGINT REFERENCES sends(id) ON DELETE SET NULL,
    decision_reason TEXT,
    request_channel_id BIGINT,
    request_message_id BIGINT,
    approved_by_user_id BIGINT,
    approved_send_id BIGINT REFERENCES sends(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at TIMESTAMPTZ,
    CHECK (action IN ('send_add', 'send_remove')),
    CHECK (status IN ('pending', 'approved', 'rejected', 'failed'))
);

CREATE INDEX IF NOT EXISTS idx_send_change_requests_pending
ON send_change_requests (status, created_at, id);

CREATE INDEX IF NOT EXISTS idx_send_change_requests_domme
ON send_change_requests (guild_id, domme_user_id, status, created_at DESC);

INSERT INTO db_build_version (version, notes)
VALUES (
    '006_send_change_requests',
    'Backend send-change approval requests for Bash rob operations'
)
ON CONFLICT (version) DO NOTHING;
