-- Dev runtime grants for dev_rob_bot on rob_dev_v2.
-- Run manually as doadmin.

\connect rob_dev_v2

GRANT CONNECT ON DATABASE rob_dev_v2 TO dev_rob_bot;
GRANT USAGE ON SCHEMA public TO dev_rob_bot;
GRANT SELECT, INSERT, UPDATE, DELETE
ON ALL TABLES IN SCHEMA public
TO dev_rob_bot;
GRANT USAGE, SELECT, UPDATE
ON ALL SEQUENCES IN SCHEMA public
TO dev_rob_bot;

REVOKE CREATE ON SCHEMA public FROM dev_rob_bot;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO dev_rob_bot;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO dev_rob_bot;
