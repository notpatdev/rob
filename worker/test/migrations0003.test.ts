import { describe, expect, it } from "vitest";
import { applyD1Migrations, env } from "cloudflare:test";
import { seedGuild, seedCreator, seedNotificationChain } from "./helpers";
import { seedAlias, seedDocument, seedGlobalProfile } from "./profileHelpers";

/**
 * Mirrors `migrations.test.ts`'s approach for 0002: rewind D1 back to a
 * 0001+0002-only state (dropping only what 0003 created and forgetting its
 * bookkeeping row), insert data through the tables that already existed at
 * that point (including a `sends` row with no `sender_discord_user_id`),
 * then reapply migrations so 0003 runs for real against that populated
 * database and must not alter or lose a single existing row.
 */
describe("0003 migration additive safety", () => {
  it("applies over populated 0001+0002 data without altering or losing any existing row", async () => {
    const newTables = ["guild_setup_sessions", "profile_link_import_candidates", "profile_link_imports"];

    for (const table of newTables) {
      await env.DB.prepare(`DROP TABLE IF EXISTS ${table}`).run();
    }
    // SQLite cannot drop a single column, so rewinding `sends` back to its pre-0003 shape means
    // rebuilding the table (matching 0001's original DDL, including its foreign keys) without
    // `sender_discord_user_id`, then re-creating its original indexes.
    await env.DB.prepare(
      `CREATE TABLE sends_pre0003 (
         id TEXT PRIMARY KEY,
         event_id TEXT NOT NULL REFERENCES throne_events (id),
         guild_id TEXT NOT NULL REFERENCES guilds (guild_id),
         registration_id TEXT NOT NULL REFERENCES domme_registrations (id),
         created_at TEXT NOT NULL
       )`,
    ).run();
    await env.DB.prepare(
      "INSERT INTO sends_pre0003 (id, event_id, guild_id, registration_id, created_at) SELECT id, event_id, guild_id, registration_id, created_at FROM sends",
    ).run();
    await env.DB.prepare("DROP TABLE sends").run();
    await env.DB.prepare("ALTER TABLE sends_pre0003 RENAME TO sends").run();
    await env.DB.prepare("CREATE UNIQUE INDEX idx_sends_event_guild ON sends (event_id, guild_id)").run();
    await env.DB.prepare("CREATE INDEX idx_sends_guild ON sends (guild_id)").run();
    await env.DB.prepare("DELETE FROM d1_migrations WHERE name = '0003_links_and_setup.sql'").run();

    const remainingTables = await env.DB.prepare(
      "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name",
    ).all<{ name: string }>();
    const remainingNames = remainingTables.results.map((row) => row.name);
    for (const table of newTables) {
      expect(remainingNames).not.toContain(table);
    }
    expect(remainingNames).toEqual(
      expect.arrayContaining(["guilds", "throne_creators", "domme_registrations", "sends", "profile_documents", "global_profiles"]),
    );

    // Populate through the pre-0003 schema, exactly like an already-running deployment would.
    await seedGuild("910100000000000001", "910100000000000002");
    const creator = await seedCreator({ id: "creator-pre0003", ownerDiscordUserId: "910100000000000003" });
    const chain = await seedNotificationChain({
      guildId: "910100000000000001",
      creatorId: creator.id,
      senderUsername: "existing-supporter",
      amountMinor: 1500,
    });
    await seedDocument({ id: "doc-pre0003", ownerUserId: "910100000000000003", orientation: "submissive" });
    await seedAlias("doc-pre0003", "PreExisting", "preexisting");
    await seedGlobalProfile("910100000000000003", "doc-pre0003");

    const sendRowBefore = await env.DB.prepare("SELECT * FROM sends WHERE id = ?").bind(chain.sendId).first();
    const aliasRowBefore = await env.DB.prepare("SELECT * FROM profile_aliases WHERE document_id = ?")
      .bind("doc-pre0003")
      .first();

    await applyD1Migrations(env.DB, env.TEST_MIGRATIONS);

    const tablesAfter = await env.DB.prepare("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").all<{
      name: string;
    }>();
    const namesAfter = tablesAfter.results.map((row) => row.name);
    for (const table of newTables) {
      expect(namesAfter).toContain(table);
    }

    const sendRowAfter = await env.DB.prepare("SELECT * FROM sends WHERE id = ?").bind(chain.sendId).first<{
      sender_discord_user_id: string | null;
    }>();
    const aliasRowAfter = await env.DB.prepare("SELECT * FROM profile_aliases WHERE document_id = ?")
      .bind("doc-pre0003")
      .first();

    expect(sendRowAfter).toEqual({ ...(sendRowBefore as object), sender_discord_user_id: null });
    expect(aliasRowAfter).toEqual(aliasRowBefore);

    // The new tables are immediately usable.
    const now = new Date().toISOString();
    await env.DB.prepare(
      `INSERT INTO guild_setup_sessions (id, guild_id, initiator_user_id, status, current_step, revision, expires_at, created_at, updated_at)
       VALUES ('session-1', '910100000000000001', '910100000000000003', 'active', 'channel', 0, ?, ?, ?)`,
    )
      .bind(now, now, now)
      .run();
    const session = await env.DB.prepare("SELECT * FROM guild_setup_sessions WHERE id = 'session-1'").first();
    expect(session).not.toBeNull();
  });
});
