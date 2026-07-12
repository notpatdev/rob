-- Rob v2 runtime grants template.
-- Run manually as doadmin and adjust role/database names if needed.
-- Runtime roles must not receive CREATE/ALTER/DROP/TRUNCATE privileges.
-- NOTE: This template is environment-specific and is intentionally not tracked
-- in db_build_version checks. scripts/check_db.py validates runtime privileges
-- based on the active DATABASE_URL user instead.

-- ---------------------------------------------------------------------------
-- Rehearsal: prod_rob_bot on rob_dev_v2
-- ---------------------------------------------------------------------------
\connect rob_dev_v2

GRANT CONNECT ON DATABASE rob_dev_v2 TO prod_rob_bot;
GRANT USAGE ON SCHEMA public TO prod_rob_bot;
GRANT SELECT, INSERT, UPDATE, DELETE
ON ALL TABLES IN SCHEMA public
TO prod_rob_bot;
GRANT USAGE, SELECT, UPDATE
ON ALL SEQUENCES IN SCHEMA public
TO prod_rob_bot;
REVOKE CREATE ON SCHEMA public FROM prod_rob_bot;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO prod_rob_bot;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO prod_rob_bot;

-- ---------------------------------------------------------------------------
-- Rehearsal: prod_rob_webhook on rob_dev_v2 (narrower scope)
-- ---------------------------------------------------------------------------
\connect rob_dev_v2

GRANT CONNECT ON DATABASE rob_dev_v2 TO prod_rob_webhook;
GRANT USAGE ON SCHEMA public TO prod_rob_webhook;

GRANT SELECT ON
  db_build_version,
  bot_settings,
  bot_users,
  dommes,
  subs,
  sub_send_names,
  vib_settings,
  vib_leaderboard
TO prod_rob_webhook;

GRANT SELECT, INSERT, UPDATE ON
  sends,
  bot_users
TO prod_rob_webhook;

GRANT SELECT, UPDATE ON
  dommes,
  bot_settings
TO prod_rob_webhook;

GRANT USAGE, SELECT, UPDATE ON SEQUENCE sends_id_seq TO prod_rob_webhook;
GRANT USAGE, SELECT, UPDATE ON SEQUENCE bot_users_id_seq TO prod_rob_webhook;

REVOKE CREATE ON SCHEMA public FROM prod_rob_webhook;
REVOKE DELETE ON TABLE sends FROM prod_rob_webhook;
REVOKE DELETE ON TABLE bot_users FROM prod_rob_webhook;

-- ---------------------------------------------------------------------------
-- Production: prod_rob_bot on rob_prod
-- ---------------------------------------------------------------------------
\connect rob_prod

GRANT CONNECT ON DATABASE rob_prod TO prod_rob_bot;
GRANT USAGE ON SCHEMA public TO prod_rob_bot;
GRANT SELECT, INSERT, UPDATE, DELETE
ON ALL TABLES IN SCHEMA public
TO prod_rob_bot;
GRANT USAGE, SELECT, UPDATE
ON ALL SEQUENCES IN SCHEMA public
TO prod_rob_bot;
REVOKE CREATE ON SCHEMA public FROM prod_rob_bot;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO prod_rob_bot;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO prod_rob_bot;

-- ---------------------------------------------------------------------------
-- Production: prod_rob_webhook on rob_prod (narrower scope)
-- ---------------------------------------------------------------------------
\connect rob_prod

GRANT CONNECT ON DATABASE rob_prod TO prod_rob_webhook;
GRANT USAGE ON SCHEMA public TO prod_rob_webhook;

GRANT SELECT ON
  db_build_version,
  bot_settings,
  bot_users,
  dommes,
  subs,
  sub_send_names,
  vib_settings,
  vib_leaderboard
TO prod_rob_webhook;

GRANT SELECT, INSERT, UPDATE ON
  sends,
  bot_users
TO prod_rob_webhook;

GRANT SELECT, UPDATE ON
  dommes,
  bot_settings
TO prod_rob_webhook;

GRANT USAGE, SELECT, UPDATE ON SEQUENCE sends_id_seq TO prod_rob_webhook;
GRANT USAGE, SELECT, UPDATE ON SEQUENCE bot_users_id_seq TO prod_rob_webhook;

REVOKE CREATE ON SCHEMA public FROM prod_rob_webhook;
REVOKE DELETE ON TABLE sends FROM prod_rob_webhook;
REVOKE DELETE ON TABLE bot_users FROM prod_rob_webhook;

-- If webhook code needs more access, add the smallest specific grant required.
-- Do not grant CREATE, ALTER, DROP, or TRUNCATE to runtime users.
