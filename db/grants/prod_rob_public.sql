-- Prod runtime grants for prod_rob_public on rob_prod.
-- Run manually as doadmin.
--
-- This role backs the public read-only API (api.robthebot.com / GET
-- /public/sends). It is intentionally the narrowest runtime role: SELECT-only,
-- and never the webhook writer role.
--
-- Create the role first if it does not exist, e.g.:
--   CREATE ROLE prod_rob_public LOGIN PASSWORD '...';

\connect rob_prod

GRANT CONNECT ON DATABASE rob_prod TO prod_rob_public;
GRANT USAGE ON SCHEMA public TO prod_rob_public;

-- Only the tables the /public/sends response reads from:
--   sends  -> the counted sends themselves
--   dommes -> the public display label (public_display_name / throne_handle)
GRANT SELECT ON
  sends,
  dommes
TO prod_rob_public;

-- Hard stop: no writes, no DDL, ever.
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON sends FROM prod_rob_public;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON dommes FROM prod_rob_public;
REVOKE CREATE ON SCHEMA public FROM prod_rob_public;

-- Do not grant INSERT, UPDATE, DELETE, CREATE, ALTER, DROP, TRUNCATE, or any
-- sequence privileges to prod_rob_public.
