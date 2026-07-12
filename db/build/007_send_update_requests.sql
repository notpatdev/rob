-- Rob send-change approvals: allow send update requests.

ALTER TABLE send_change_requests
    DROP CONSTRAINT IF EXISTS send_change_requests_action_check;

ALTER TABLE send_change_requests
    ADD CONSTRAINT send_change_requests_action_check
    CHECK (action IN ('send_add', 'send_remove', 'send_update'));

INSERT INTO db_build_version (version, notes)
VALUES (
    '007_send_update_requests',
    'Allow send_update action in send_change_requests approval flow'
)
ON CONFLICT (version) DO NOTHING;
