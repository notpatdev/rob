import { env } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import { DraftError } from "../src/profile/draftService";
import { publishDraft } from "../src/profile/publishService";
import { seedCreator, seedGuild, TEST_HOME_GUILD_ID } from "./helpers";
import { seedDocument, seedSelection } from "./profileHelpers";

async function seedCompletedDraft(options: {
  owner: string;
  draftId: string;
  documentId: string;
  creatorId: string;
}): Promise<void> {
  await seedDocument({
    id: options.documentId,
    ownerUserId: options.owner,
    state: "draft",
    orientation: "domme",
    dmStatus: "open",
    throneCreatorId: options.creatorId,
  });
  await seedSelection(options.documentId, "pronoun", "She/Her");
  const now = new Date().toISOString();
  await env.DB.prepare("UPDATE throne_creators SET webhook_verified_at = ? WHERE id = ?")
    .bind(now, options.creatorId)
    .run();
  await env.DB.prepare(
    `INSERT INTO profile_drafts
       (id, owner_user_id, origin_guild_id, target_scope, document_id, base_version,
        status, current_step, revision, created_at, updated_at)
     VALUES (?, ?, ?, 'global', ?, 0, 'active', 'review', 4, ?, ?)`,
  )
    .bind(options.draftId, options.owner, TEST_HOME_GUILD_ID, options.documentId, now, now)
    .run();
  for (const step of [
    "orientation",
    "identity",
    "links",
    "throne",
    "identity_dm_status_selected",
  ]) {
    await env.DB.prepare(
      `INSERT INTO profile_draft_steps (draft_id, step_key, status, completed_at)
       VALUES (?, ?, 'completed', ?)`,
    )
      .bind(options.draftId, step, now)
      .run();
  }
}

describe("atomic publication registration projection", () => {
  it("publishes the profile and registration together", async () => {
    const owner = "950000000000000001";
    await seedGuild(TEST_HOME_GUILD_ID);
    const creator = await seedCreator({
      id: "publish-projection-creator",
      ownerDiscordUserId: owner,
    });
    await seedCompletedDraft({
      owner,
      draftId: "publish-projection-draft",
      documentId: "publish-projection-document",
      creatorId: creator.id,
    });

    await publishDraft(env, {
      draftId: "publish-projection-draft",
      ownerUserId: owner,
      expectedRevision: 4,
    });

    const row = await env.DB.prepare(
      `SELECT creator_id, active, profile_managed
         FROM domme_registrations
        WHERE guild_id = ? AND discord_user_id = ?`,
    )
      .bind(TEST_HOME_GUILD_ID, owner)
      .first<{ creator_id: string; active: number; profile_managed: number }>();
    expect(row).toEqual({ creator_id: creator.id, active: 1, profile_managed: 1 });
  });

  it("rejects a conflicting legacy registration without publishing", async () => {
    const owner = "950000000000000002";
    await seedGuild(TEST_HOME_GUILD_ID);
    const [profileCreator, legacyCreator] = await Promise.all([
      seedCreator({
        id: "publish-conflict-profile-creator",
        ownerDiscordUserId: owner,
      }),
      seedCreator({
        id: "publish-conflict-legacy-creator",
        ownerDiscordUserId: owner,
      }),
    ]);
    await seedCompletedDraft({
      owner,
      draftId: "publish-conflict-draft",
      documentId: "publish-conflict-document",
      creatorId: profileCreator.id,
    });
    const now = new Date().toISOString();
    await env.DB.prepare(
      `INSERT INTO domme_registrations
         (id, guild_id, creator_id, discord_user_id, active, profile_managed, created_at, updated_at)
       VALUES ('publish-conflict-legacy', ?, ?, ?, 1, 0, ?, ?)`,
    )
      .bind(TEST_HOME_GUILD_ID, legacyCreator.id, owner, now, now)
      .run();

    await expect(
      publishDraft(env, {
        draftId: "publish-conflict-draft",
        ownerUserId: owner,
        expectedRevision: 4,
      }),
    ).rejects.toEqual(
      expect.objectContaining<Partial<DraftError>>({
        code: "legacy_registration_conflict",
        status: 409,
      }),
    );
    const document = await env.DB.prepare(
      "SELECT state FROM profile_documents WHERE id = 'publish-conflict-document'",
    ).first<{ state: string }>();
    const root = await env.DB.prepare("SELECT 1 FROM global_profiles WHERE owner_user_id = ?")
      .bind(owner)
      .first();
    expect(document?.state).toBe("draft");
    expect(root).toBeNull();
  });
});
