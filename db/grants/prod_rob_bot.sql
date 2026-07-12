-- Prod runtime grants for prod_rob_bot on rob_prod.
-- Run manually as doadmin.

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
