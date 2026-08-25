-- Rob v2 DB Build Script
-- Apply manually as doadmin after 010_leaderboard_access_role.sql.
-- No new runtime grants are required (the webhook role already has INSERT on sends).
--
-- Adds the partial unique index on sends.fallback_event_hash that the v2 schema
-- was missing. Throne delivers webhooks at-least-once, so a retried event that
-- arrives WITHOUT a stable event_id would otherwise insert a second row -> the
-- Dom/me is credited twice and the send card is posted twice. The application
-- already de-duplicates by catching unique violations
-- (rob/database/repositories/sends.py); this index supplies the missing
-- constraint for the no-event_id path.
--
-- The index is scoped to ``event_id IS NULL``: rows that DO carry an event_id are
-- de-duplicated by idx_sends_event_id_unique (from 002), and parse_throne_send
-- populates fallback_event_hash on every row, so a broader predicate would make
-- two genuinely distinct events (different event_id, same order/item/amount/
-- gifter/timestamp) collide and drop the second as a false duplicate.

-- Neutralise any pre-existing duplicate hashes among event-less rows first so the
-- unique index can be built without deleting historical (already-posted) rows.
-- Only the LATER duplicate row(s) per hash have their dedup key cleared; the
-- earliest row per hash keeps it. No financial rows are removed.
WITH ranked AS (
    SELECT
        id,
        row_number() OVER (
            PARTITION BY fallback_event_hash
            ORDER BY id
        ) AS rn
    FROM sends
    WHERE fallback_event_hash IS NOT NULL
      AND event_id IS NULL
)
UPDATE sends
SET fallback_event_hash = NULL
FROM ranked
WHERE sends.id = ranked.id
  AND ranked.rn > 1;

CREATE UNIQUE INDEX IF NOT EXISTS idx_sends_fallback_hash_unique
ON sends (fallback_event_hash)
WHERE event_id IS NULL AND fallback_event_hash IS NOT NULL;

INSERT INTO db_build_version (version, notes)
VALUES (
    '011_send_fallback_hash_unique',
    'Partial unique index on sends.fallback_event_hash for at-least-once webhook dedup'
)
ON CONFLICT (version) DO NOTHING;
