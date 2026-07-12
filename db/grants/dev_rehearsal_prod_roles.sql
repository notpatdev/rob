-- Dev rehearsal runtime grants on rob_dev_v2 using production-shaped role names.
-- Run manually as doadmin.

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

-- Do not grant CREATE, ALTER, DROP, or TRUNCATE to runtime users.
