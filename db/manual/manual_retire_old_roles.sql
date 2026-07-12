-- Manual helper to retire old Rob roles once new runtime users are active.
-- Review dependencies before dropping any role.

-- Inspect old roles and ownership references:
SELECT rolname, rolcanlogin
FROM pg_roles
WHERE rolname IN ('rob_dev_app', 'rob_migration')
ORDER BY rolname;

SELECT schemaname, tablename, tableowner
FROM pg_tables
WHERE schemaname = 'public'
  AND tableowner IN ('rob_dev_app', 'rob_migration')
ORDER BY tablename;

-- If ownership still points to old roles, transfer first:
-- ALTER TABLE public.<table_name> OWNER TO doadmin;
-- ALTER SEQUENCE public.<sequence_name> OWNER TO doadmin;

-- Optional final cleanup (uncomment deliberately):
-- REASSIGN OWNED BY rob_dev_app TO doadmin;
-- DROP OWNED BY rob_dev_app;
-- DROP ROLE rob_dev_app;
--
-- REASSIGN OWNED BY rob_migration TO doadmin;
-- DROP OWNED BY rob_migration;
-- DROP ROLE rob_migration;

