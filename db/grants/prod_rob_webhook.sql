-- Prod runtime grants for prod_rob_webhook on rob_prod.
-- Run manually as doadmin.
-- This user is intentionally narrower than prod_rob_bot.

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

GRANT USAGE, SELECT, UPDATE
ON SEQUENCE sends_id_seq
TO prod_rob_webhook;

GRANT USAGE, SELECT, UPDATE
ON SEQUENCE bot_users_id_seq
TO prod_rob_webhook;

REVOKE CREATE ON SCHEMA public FROM prod_rob_webhook;
REVOKE DELETE ON TABLE sends FROM prod_rob_webhook;
REVOKE DELETE ON TABLE bot_users FROM prod_rob_webhook;

-- Do not grant CREATE, ALTER, DROP, or TRUNCATE to prod_rob_webhook.
