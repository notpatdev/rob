# Rob v2 DB Build Scripts

These are **manual DB build scripts** for Rob v2.

- Run them manually in pgAdmin4 or `psql` as `doadmin`.
- For production bootstrap, use `../manual/setup_rob_prod.sql`.
- For rehearsals, start with `rob_dev_v2` first.

Run in order:

1. `001_core_schema.sql`
2. `002_indexes.sql`
3. `004_sub_send_names.sql`
4. `005_count_recovery.sql`
5. `006_send_change_requests.sql`
6. `007_send_update_requests.sql`
7. `008_dm_preferences.sql`
8. `009_terms_acceptance.sql`
9. `010_leaderboard_access_role.sql`
10. `011_send_fallback_hash_unique.sql`
11. `012_inactivity_backup_settings.sql`
12. `013_server_backups.sql`
13. `../grants/dev_rehearsal_prod_roles.sql`

`003_achievements.sql` is retired. `008_dm_preferences.sql` drops the
achievements tables/sequences if they still exist, removes the prior
`003_achievements` row from `db_build_version`, and installs the DM
notification preference columns and `domme_onboarding_state` table.
`009_terms_acceptance.sql` adds the test-guild Terms acceptance table.
`010_leaderboard_access_role.sql` adds `vib_settings.leaderboard_view_role_id`
for test-guild leaderboard access gating (no new grant needed — it is a column
on the already-granted `vib_settings` table).
`012_inactivity_backup_settings.sql` adds the `vib_settings` active /
unverified / trial-mod role columns plus `backup_approval_channel_id` (all
columns on the already-granted `vib_settings` table). `013_server_backups.sql`
adds the `server_backups` and `server_backup_approvals` tables for the hourly
server-backup system — re-run the relevant grants file afterwards so the runtime
bot user can use the new tables/sequences.

Important:

- Do not run prod grants against dev databases.
- Re-run the relevant grants file after adding new build files so runtime users can access new tables.
- Do not run manual cleanup scripts unless you are intentionally cleaning an old database.
- These are DB build scripts, not runtime migrations.
