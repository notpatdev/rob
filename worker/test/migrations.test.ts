import { describe, expect, it } from "vitest";
import { applyD1Migrations, env } from "cloudflare:test";
import { seedGuild, seedCreator, seedNotificationChain } from "./helpers";

/**
 * `vitest.config.ts`'s global setup already applies every migration (0001
 * and 0002) before each test file, so by the time a normal test runs the
 * profile tables already exist. To actually exercise "0002 applies cleanly
 * on top of an already-populated 0001 database", this test rewinds D1 back
 * to a 0001-only state -- drop 0002's tables and forget its bookkeeping row
 * -- inserts data through the *existing* 0001 tables, and then reapplies
 * migrations so 0002 runs for real against that populated database.
 */
describe("0002 migration additive safety", () => {
  it("applies over populated 0001 data without altering or losing any existing row", async () => {
    const profileTables = [
      "profile_draft_steps",
      "profile_drafts",
      "profile_publications",
      "server_profiles",
      "global_profiles",
      "profile_link_visibility",
      "profile_document_overrides",
      "profile_links",
      "profile_aliases",
      "profile_document_selections",
      "profile_documents",
    ];

    // Rewind: drop everything 0002 created and forget that it ran.
    for (const table of profileTables) {
      await env.DB.prepare(`DROP TABLE IF EXISTS ${table}`).run();
    }
    await env.DB.prepare("DELETE FROM d1_migrations WHERE name = '0002_profile_system.sql'").run();

    const remainingTables = await env.DB.prepare(
      "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name",
    ).all<{ name: string }>();
    const remainingNames = remainingTables.results.map((row) => row.name);
    for (const table of profileTables) {
      expect(remainingNames).not.toContain(table);
    }
    // 0001's tables must still be intact after the rewind.
    expect(remainingNames).toEqual(
      expect.arrayContaining(["guilds", "throne_creators", "domme_registrations", "throne_events", "sends", "notifications"]),
    );

    // Populate the 0001-only schema exactly like a live, already-running deployment would.
    await seedGuild("900000000000000001", "900000000000000002");
    const creator = await seedCreator({ id: "creator-preexisting", ownerDiscordUserId: "900000000000000003" });
    const chain = await seedNotificationChain({
      guildId: "900000000000000001",
      creatorId: creator.id,
      senderUsername: "existing-supporter",
      amountMinor: 2500,
    });

    const guildRowBefore = await env.DB.prepare("SELECT * FROM guilds WHERE guild_id = ?")
      .bind("900000000000000001")
      .first();
    const creatorRowBefore = await env.DB.prepare("SELECT * FROM throne_creators WHERE id = ?")
      .bind(creator.id)
      .first();
    const sendRowBefore = await env.DB.prepare("SELECT * FROM sends WHERE id = ?").bind(chain.sendId).first();
    const notificationRowBefore = await env.DB.prepare("SELECT * FROM notifications WHERE id = ?")
      .bind(chain.notificationId)
      .first();

    // Reapply migrations: 0001 is already recorded as applied and is skipped;
    // 0002 is not, so it runs for real against this populated database.
    await applyD1Migrations(env.DB, env.TEST_MIGRATIONS);

    const tablesAfter = await env.DB.prepare("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").all<{
      name: string;
    }>();
    const namesAfter = tablesAfter.results.map((row) => row.name);
    for (const table of profileTables) {
      expect(namesAfter).toContain(table);
    }

    const guildRowAfter = await env.DB.prepare("SELECT * FROM guilds WHERE guild_id = ?")
      .bind("900000000000000001")
      .first();
    const creatorRowAfter = await env.DB.prepare("SELECT * FROM throne_creators WHERE id = ?")
      .bind(creator.id)
      .first();
    const sendRowAfter = await env.DB.prepare("SELECT * FROM sends WHERE id = ?").bind(chain.sendId).first();
    const notificationRowAfter = await env.DB.prepare("SELECT * FROM notifications WHERE id = ?")
      .bind(chain.notificationId)
      .first();

    expect(guildRowAfter).toEqual(guildRowBefore);
    expect(creatorRowAfter).toEqual(creatorRowBefore);
    expect(sendRowAfter).toEqual(sendRowBefore);
    expect(notificationRowAfter).toEqual(notificationRowBefore);

    // And the new profile schema is immediately usable.
    const now = new Date().toISOString();
    await env.DB.prepare(
      `INSERT INTO profile_documents (id, owner_user_id, state, orientation, dm_status, created_at, updated_at)
       VALUES ('doc-1', '900000000000000003', 'draft', 'domme', 'open', ?, ?)`,
    )
      .bind(now, now)
      .run();
    const doc = await env.DB.prepare("SELECT * FROM profile_documents WHERE id = 'doc-1'").first();
    expect(doc).not.toBeNull();
  });
});
