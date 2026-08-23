import { describe, expect, it } from "vitest";
import { applyD1Migrations, env } from "cloudflare:test";
import { authHeaders, callWorker, jsonRequest, readJson, TEST_HOME_GUILD_ID } from "./helpers";
import { seedAlias, seedDocument, seedGlobalProfile, seedLink, seedSelection } from "./profileHelpers";

/** Inserts a document through the *pre-0004* column list, since `seedDocument` (like the Worker
 * itself) now always writes `profile_color`. */
async function seedPre0004Document(id: string, ownerUserId: string, state: string, bio: string | null): Promise<void> {
  const now = new Date().toISOString();
  await env.DB.prepare(
    `INSERT INTO profile_documents
       (id, owner_user_id, state, orientation, dm_status, bio, public_send_stats,
        throne_creator_id, preferred_payment_link_id, created_at, updated_at)
     VALUES (?, ?, ?, 'domme', 'open', ?, 0, NULL, NULL, ?, ?)`,
  )
    .bind(id, ownerUserId, state, bio, now, now)
    .run();
}

/**
 * Mirrors `migrations.test.ts` (0002) and `migrations0003.test.ts` (0003):
 * rewind D1 to an exact pre-0004 shape, populate it through the schema an
 * already-running deployment would have, then replay migrations so 0004
 * applies for real over populated 0001-0003 data. 0004 only adds nullable
 * columns, so every pre-existing row must survive byte-for-byte apart from
 * the new NULL columns, and every pre-existing draft must still resume.
 */
describe("0004 migration additive safety", () => {
  it("applies over populated 0001-0003 data, preserving every row and back-filling only NULLs", async () => {
    // SQLite cannot drop a column, so rebuild both altered tables at their
    // exact pre-0004 shapes, copy the data across, and forget 0004 ever ran.
    await env.DB.prepare(
      `CREATE TABLE profile_documents_pre0004 (
         id TEXT PRIMARY KEY,
         owner_user_id TEXT NOT NULL,
         state TEXT NOT NULL CHECK (state IN ('draft', 'published', 'superseded')),
         orientation TEXT CHECK (orientation IN ('domme', 'submissive', 'switch_domme', 'switch_submissive')),
         dm_status TEXT CHECK (dm_status IN ('open', 'by_request', 'after_tribute', 'closed')),
         bio TEXT CHECK (bio IS NULL OR length(bio) <= 300),
         public_send_stats INTEGER NOT NULL DEFAULT 0 CHECK (public_send_stats IN (0, 1)),
         throne_creator_id TEXT,
         preferred_payment_link_id TEXT,
         created_at TEXT NOT NULL,
         updated_at TEXT NOT NULL
       )`,
    ).run();
    await env.DB.prepare(
      `INSERT INTO profile_documents_pre0004
         (id, owner_user_id, state, orientation, dm_status, bio, public_send_stats,
          throne_creator_id, preferred_payment_link_id, created_at, updated_at)
       SELECT id, owner_user_id, state, orientation, dm_status, bio, public_send_stats,
              throne_creator_id, preferred_payment_link_id, created_at, updated_at
         FROM profile_documents`,
    ).run();
    await env.DB.prepare("DROP TABLE profile_documents").run();
    await env.DB.prepare("ALTER TABLE profile_documents_pre0004 RENAME TO profile_documents").run();
    await env.DB.prepare("CREATE INDEX idx_profile_documents_owner ON profile_documents (owner_user_id)").run();
    await env.DB.prepare(
      "CREATE INDEX idx_profile_documents_owner_state ON profile_documents (owner_user_id, state)",
    ).run();

    await env.DB.prepare(
      `CREATE TABLE profile_drafts_pre0004 (
         id TEXT PRIMARY KEY,
         owner_user_id TEXT NOT NULL,
         origin_guild_id TEXT,
         target_scope TEXT NOT NULL CHECK (target_scope IN ('global', 'server')),
         guild_id TEXT,
         server_mode TEXT CHECK (server_mode IN ('linked', 'independent')),
         document_id TEXT NOT NULL REFERENCES profile_documents (id),
         base_version INTEGER NOT NULL DEFAULT 0,
         status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'published')),
         current_step TEXT NOT NULL DEFAULT 'orientation',
         revision INTEGER NOT NULL DEFAULT 0,
         intro_message_id TEXT,
         wizard_message_id TEXT,
         created_at TEXT NOT NULL,
         updated_at TEXT NOT NULL,
         published_at TEXT
       )`,
    ).run();
    await env.DB.prepare(
      `INSERT INTO profile_drafts_pre0004
         (id, owner_user_id, origin_guild_id, target_scope, guild_id, server_mode, document_id,
          base_version, status, current_step, revision, intro_message_id, wizard_message_id,
          created_at, updated_at, published_at)
       SELECT id, owner_user_id, origin_guild_id, target_scope, guild_id, server_mode, document_id,
              base_version, status, current_step, revision, intro_message_id, wizard_message_id,
              created_at, updated_at, published_at
         FROM profile_drafts`,
    ).run();
    await env.DB.prepare("DROP TABLE profile_drafts").run();
    await env.DB.prepare("ALTER TABLE profile_drafts_pre0004 RENAME TO profile_drafts").run();
    await env.DB.prepare(
      `CREATE UNIQUE INDEX idx_profile_drafts_active_global
         ON profile_drafts (owner_user_id)
        WHERE target_scope = 'global' AND status = 'active'`,
    ).run();
    await env.DB.prepare(
      `CREATE UNIQUE INDEX idx_profile_drafts_active_server
         ON profile_drafts (guild_id, owner_user_id)
        WHERE target_scope = 'server' AND status = 'active'`,
    ).run();
    await env.DB.prepare("CREATE INDEX idx_profile_drafts_owner ON profile_drafts (owner_user_id)").run();
    await env.DB.prepare("DELETE FROM d1_migrations WHERE name = '0004_profile_color_and_wizard_stage.sql'").run();

    const documentColumnsBefore = await env.DB.prepare("PRAGMA table_info(profile_documents)").all<{ name: string }>();
    expect(documentColumnsBefore.results.map((row) => row.name)).not.toContain("profile_color");
    const draftColumnsBefore = await env.DB.prepare("PRAGMA table_info(profile_drafts)").all<{ name: string }>();
    expect(draftColumnsBefore.results.map((row) => row.name)).not.toContain("wizard_stage");

    // Populate through the pre-0004 schema, exactly like a live deployment would:
    // one published global profile and one half-finished active draft.
    const owner = "960000000000000001";
    await seedPre0004Document("doc-pre0004-published", owner, "published", "pre-existing bio");
    await seedSelection("doc-pre0004-published", "pronoun", "She/Her");
    await seedAlias("doc-pre0004-published", "PreExisting", "preexisting");
    await seedLink({
      id: "link-pre0004",
      documentId: "doc-pre0004-published",
      platform: "bluesky",
      publicLabel: "Bluesky",
      normalizedUrl: "https://bsky.app/profile/pre.test",
      linkType: "social",
    });
    await seedGlobalProfile(owner, "doc-pre0004-published", 2);

    await seedPre0004Document("doc-pre0004-draft", owner, "draft", null);
    const now = new Date().toISOString();
    await env.DB.prepare(
      `INSERT INTO profile_drafts
         (id, owner_user_id, origin_guild_id, target_scope, guild_id, server_mode, document_id,
          base_version, status, current_step, revision, created_at, updated_at)
       VALUES (?, ?, ?, 'global', NULL, NULL, ?, 2, 'active', 'identity', 4, ?, ?)`,
    )
      .bind("draft-pre0004", owner, TEST_HOME_GUILD_ID, "doc-pre0004-draft", now, now)
      .run();
    await env.DB.prepare(
      `INSERT INTO profile_draft_steps (draft_id, step_key, status, completed_at)
       VALUES ('draft-pre0004', 'orientation', 'completed', ?), ('draft-pre0004', 'identity', 'completed', ?)`,
    )
      .bind(now, now)
      .run();

    const publishedBefore = await env.DB.prepare("SELECT * FROM profile_documents WHERE id = ?")
      .bind("doc-pre0004-published")
      .first();
    const draftDocBefore = await env.DB.prepare("SELECT * FROM profile_documents WHERE id = ?")
      .bind("doc-pre0004-draft")
      .first();
    const draftBefore = await env.DB.prepare("SELECT * FROM profile_drafts WHERE id = ?")
      .bind("draft-pre0004")
      .first();
    const linkBefore = await env.DB.prepare("SELECT * FROM profile_links WHERE id = ?").bind("link-pre0004").first();
    const aliasBefore = await env.DB.prepare("SELECT * FROM profile_aliases WHERE document_id = ?")
      .bind("doc-pre0004-published")
      .first();
    const documentCountBefore = await env.DB.prepare("SELECT COUNT(*) AS count FROM profile_documents").first<{
      count: number;
    }>();

    await applyD1Migrations(env.DB, env.TEST_MIGRATIONS);

    // Only the new nullable columns appear; nothing is dropped or renamed.
    const documentColumnsAfter = (await env.DB.prepare("PRAGMA table_info(profile_documents)").all<{ name: string }>())
      .results.map((row) => row.name);
    expect(documentColumnsAfter).toEqual([...documentColumnsBefore.results.map((row) => row.name), "profile_color"]);
    const draftColumnsAfter = (await env.DB.prepare("PRAGMA table_info(profile_drafts)").all<{ name: string }>()).results
      .map((row) => row.name);
    expect(draftColumnsAfter).toEqual([
      ...draftColumnsBefore.results.map((row) => row.name),
      "wizard_stage",
      "wizard_substep",
    ]);

    expect(await env.DB.prepare("SELECT * FROM profile_documents WHERE id = ?").bind("doc-pre0004-published").first())
      .toEqual({ ...(publishedBefore as object), profile_color: null });
    expect(await env.DB.prepare("SELECT * FROM profile_documents WHERE id = ?").bind("doc-pre0004-draft").first())
      .toEqual({ ...(draftDocBefore as object), profile_color: null });
    expect(await env.DB.prepare("SELECT * FROM profile_drafts WHERE id = ?").bind("draft-pre0004").first()).toEqual({
      ...(draftBefore as object),
      wizard_stage: null,
      wizard_substep: null,
    });
    expect(await env.DB.prepare("SELECT * FROM profile_links WHERE id = ?").bind("link-pre0004").first()).toEqual(
      linkBefore,
    );
    expect(
      await env.DB.prepare("SELECT * FROM profile_aliases WHERE document_id = ?").bind("doc-pre0004-published").first(),
    ).toEqual(aliasBefore);
    expect(
      (await env.DB.prepare("SELECT COUNT(*) AS count FROM profile_documents").first<{ count: number }>())?.count,
    ).toBe(documentCountBefore?.count);
  });

  it("enforces the sRGB range on the new colour column and accepts NULL as a real value", async () => {
    await seedDocument({ id: "doc-0004-range", ownerUserId: "960000000000000002", orientation: "domme" });

    await expect(
      env.DB.prepare("UPDATE profile_documents SET profile_color = ? WHERE id = ?")
        .bind(-1, "doc-0004-range")
        .run(),
    ).rejects.toThrow();
    await expect(
      env.DB.prepare("UPDATE profile_documents SET profile_color = ? WHERE id = ?")
        .bind(0x1000000, "doc-0004-range")
        .run(),
    ).rejects.toThrow();

    for (const value of [0, 0x5865f2, 0xffffff, null]) {
      await env.DB.prepare("UPDATE profile_documents SET profile_color = ? WHERE id = ?")
        .bind(value, "doc-0004-range")
        .run();
      const row = await env.DB.prepare("SELECT profile_color FROM profile_documents WHERE id = ?")
        .bind("doc-0004-range")
        .first<{ profile_color: number | null }>();
      expect(row?.profile_color).toBe(value);
    }
  });

  it("leaves the wizard columns free-form so the bot's vocabulary can grow without a migration", async () => {
    const owner = "960000000000000003";
    await seedDocument({ id: "doc-0004-vocab", ownerUserId: owner, state: "draft", orientation: "domme" });
    const now = new Date().toISOString();
    await env.DB.prepare(
      `INSERT INTO profile_drafts
         (id, owner_user_id, origin_guild_id, target_scope, guild_id, server_mode, document_id,
          base_version, status, current_step, revision, created_at, updated_at)
       VALUES ('draft-0004-vocab', ?, ?, 'global', NULL, NULL, 'doc-0004-vocab', 0, 'active', 'identity', 0, ?, ?)`,
    )
      .bind(owner, TEST_HOME_GUILD_ID, now, now)
      .run();
    await env.DB.prepare(
      "INSERT INTO profile_draft_steps (draft_id, step_key, status, completed_at) VALUES ('draft-0004-vocab', 'orientation', 'completed', ?)",
    )
      .bind(now)
      .run();

    await env.DB.prepare(
      "UPDATE profile_drafts SET wizard_stage = 'some_future_screen', wizard_substep = 'step-2' WHERE id = ?",
    )
      .bind("draft-0004-vocab")
      .run();
    const row = await env.DB.prepare("SELECT wizard_stage, wizard_substep FROM profile_drafts WHERE id = ?")
      .bind("draft-0004-vocab")
      .first<{ wizard_stage: string; wizard_substep: string }>();
    expect(row).toEqual({ wizard_stage: "some_future_screen", wizard_substep: "step-2" });

    // The Worker, not the schema, is what rejects an unknown stage -- and the draft
    // contract ignores the unknown stored value rather than echoing it back.
    const response = await callWorker(
      jsonRequest(
        "GET",
        `/v1/profile-drafts/draft-0004-vocab?owner_user_id=${owner}`,
        undefined,
        authHeaders(),
      ),
    );
    const parsed = await readJson<{ data: { draft: { wizard_stage: string; wizard_substep: string | null } } }>(
      response,
    );
    expect(parsed.data.draft.wizard_stage).toBe("pronouns");
    expect(parsed.data.draft.wizard_substep).toBe("step-2");
  });

  it("lets a draft created before 0004 resume, and start recording a bookmark", async () => {
    const owner = "960000000000000004";
    await seedDocument({ id: "doc-0004-resume", ownerUserId: owner, state: "draft", orientation: "domme" });
    const now = new Date().toISOString();
    await env.DB.prepare(
      `INSERT INTO profile_drafts
         (id, owner_user_id, origin_guild_id, target_scope, guild_id, server_mode, document_id,
          base_version, status, current_step, revision, created_at, updated_at)
       VALUES ('draft-0004-resume', ?, ?, 'global', NULL, NULL, 'doc-0004-resume', 0, 'active', 'links', 7, ?, ?)`,
    )
      .bind(owner, TEST_HOME_GUILD_ID, now, now)
      .run();
    for (const step of ["orientation", "identity", "links"]) {
      await env.DB.prepare(
        "INSERT INTO profile_draft_steps (draft_id, step_key, status, completed_at) VALUES ('draft-0004-resume', ?, 'completed', ?)",
      )
        .bind(step, now)
        .run();
    }

    const before = await callWorker(
      jsonRequest("GET", `/v1/profile-drafts/draft-0004-resume?owner_user_id=${owner}`, undefined, authHeaders()),
    );
    const beforeDraft = (
      await readJson<{ data: { draft: { wizard_stage: string; wizard_substep: string | null; revision: number } } }>(
        before,
      )
    ).data.draft;
    // current_step = links, next pending step = throne, so the derived resume screen is Throne.
    expect(beforeDraft.wizard_stage).toBe("throne");
    expect(beforeDraft.wizard_substep).toBeNull();

    const moved = await callWorker(
      jsonRequest(
        "PUT",
        "/v1/profile-drafts/draft-0004-resume/wizard-stage",
        { owner_user_id: owner, expected_revision: 7, stage: "profile_color" },
        authHeaders(),
      ),
    );
    expect(moved.status).toBe(200);
    const movedDraft = (await readJson<{ data: { draft: { wizard_stage: string; revision: number } } }>(moved)).data
      .draft;
    expect(movedDraft.wizard_stage).toBe("profile_color");
    expect(movedDraft.revision).toBe(8);
    expect(
      await env.DB.prepare("SELECT wizard_stage FROM profile_drafts WHERE id = ?")
        .bind("draft-0004-resume")
        .first<{ wizard_stage: string }>(),
    ).toEqual({ wizard_stage: "profile_color" });
  });
});
