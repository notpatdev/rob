\echo Setting up rob_prod with runtime users prod_rob_bot and prod_rob_webhook.
\echo Run this manually as doadmin from psql. Example:
\echo psql postgresql://doadmin@<host>:25060/defaultdb -v prod_rob_bot_password='...' -v prod_rob_webhook_password='...' -f db/manual/setup_rob_prod.sql

\if :{?prod_rob_bot_password}
\else
  \echo Missing required psql variable: prod_rob_bot_password
  \quit 1
\endif

\if :{?prod_rob_webhook_password}
\else
  \echo Missing required psql variable: prod_rob_webhook_password
  \quit 1
\endif

SELECT format(
  'CREATE ROLE prod_rob_bot LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT',
  :'prod_rob_bot_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'prod_rob_bot')
\gexec

ALTER ROLE prod_rob_bot LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD :'prod_rob_bot_password';

SELECT format(
  'CREATE ROLE prod_rob_webhook LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT',
  :'prod_rob_webhook_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'prod_rob_webhook')
\gexec

ALTER ROLE prod_rob_webhook LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD :'prod_rob_webhook_password';

SELECT 'CREATE DATABASE rob_prod OWNER doadmin'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'rob_prod')
\gexec

\connect rob_prod

\ir ../build/001_core_schema.sql
\ir ../build/002_indexes.sql
\ir ../build/004_sub_send_names.sql
\ir ../build/005_count_recovery.sql
\ir ../build/006_send_change_requests.sql
\ir ../build/007_send_update_requests.sql
\ir ../build/008_dm_preferences.sql
\ir ../build/009_terms_acceptance.sql
\ir ../build/010_leaderboard_access_role.sql
\ir ../build/011_send_fallback_hash_unique.sql
\ir ../build/012_inactivity_backup_settings.sql
\ir ../build/013_server_backups.sql
\ir ../grants/prod_rob_bot.sql
\ir ../grants/prod_rob_webhook.sql

\echo rob_prod bootstrap complete.
