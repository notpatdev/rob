-- Manual cleanup helper for legacy dev databases.
-- Review each section carefully before running.

-- 1) Inspect legacy table counts
SELECT 'leaderboard_message' AS table_name, COUNT(*) AS rows
FROM leaderboard_message
UNION ALL
SELECT 'leaderboard_messages' AS table_name, COUNT(*) AS rows
FROM leaderboard_messages;

-- 2) Compare old-vs-new leaderboard rows (safe read-only check)
SELECT *
FROM leaderboard_messages
EXCEPT
SELECT *
FROM leaderboard_message;

-- 3) Merge any missing rows from legacy plural table
INSERT INTO leaderboard_message (
  guild_id,
  message_key,
  leaderboard_type,
  channel_id,
  message_id,
  created_at,
  updated_at
)
SELECT
  guild_id,
  message_key,
  leaderboard_type,
  channel_id,
  message_id,
  created_at,
  updated_at
FROM leaderboard_messages old
ON CONFLICT (guild_id, message_key) DO NOTHING;

-- 4) Manual destructive step (uncomment only after verification)
-- DROP TABLE leaderboard_messages;

-- 5) Optional cleanup of known test sends
-- DELETE FROM sends WHERE is_test_send = true;
-- DELETE FROM sends WHERE lower(COALESCE(sub_name, '')) IN ('marie_123');

