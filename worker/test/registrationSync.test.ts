import { env } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import { syncRegistrationForGuild } from "../src/profile/registrationSync";
import { seedCreator, seedGuild } from "./helpers";
import { seedDocument, seedGlobalProfile } from "./profileHelpers";

const HOME_GUILD = "100000000000000001";

describe("profile registration projection", () => {
  it("creates a profile-managed registration for a connected profile", async () => {
    const owner = "920000000000000001";
    await seedGuild(HOME_GUILD, "920000000000000002");
    const creator = await seedCreator({
      id: "sync-creator-connected",
      ownerDiscordUserId: owner,
    });
    await seedDocument({
      id: "sync-doc-connected",
      ownerUserId: owner,
      orientation: "domme",
      throneCreatorId: creator.id,
    });
    await seedGlobalProfile(owner, "sync-doc-connected");

    await syncRegistrationForGuild(env, HOME_GUILD, owner);

    const row = await env.DB.prepare(
      `SELECT creator_id, active, profile_managed
         FROM domme_registrations
        WHERE guild_id = ? AND discord_user_id = ?`,
    )
      .bind(HOME_GUILD, owner)
      .first<{ creator_id: string; active: number; profile_managed: number }>();
    expect(row).toEqual({
      creator_id: creator.id,
      active: 1,
      profile_managed: 1,
    });
  });

  it("deactivates only profile-managed registration after disconnect", async () => {
    const owner = "920000000000000003";
    await seedGuild(HOME_GUILD, "920000000000000004");
    const creator = await seedCreator({
      id: "sync-creator-disconnected",
      ownerDiscordUserId: owner,
    });
    await seedDocument({
      id: "sync-doc-disconnected",
      ownerUserId: owner,
      orientation: "submissive",
    });
    await seedGlobalProfile(owner, "sync-doc-disconnected");
    const now = new Date().toISOString();
    await env.DB.prepare(
      `INSERT INTO domme_registrations
         (id, guild_id, creator_id, discord_user_id, active, profile_managed, created_at, updated_at)
       VALUES ('sync-managed', ?, ?, ?, 1, 1, ?, ?)`,
    )
      .bind(HOME_GUILD, creator.id, owner, now, now)
      .run();

    await syncRegistrationForGuild(env, HOME_GUILD, owner);

    const active = await env.DB.prepare(
      "SELECT active FROM domme_registrations WHERE id = 'sync-managed'",
    ).first<{ active: number }>();
    expect(active?.active).toBe(0);
  });

  it("never deactivates an explicit legacy registration", async () => {
    const owner = "920000000000000005";
    await seedGuild(HOME_GUILD, "920000000000000006");
    const creator = await seedCreator({
      id: "sync-creator-legacy",
      ownerDiscordUserId: owner,
    });
    await seedDocument({
      id: "sync-doc-legacy",
      ownerUserId: owner,
      orientation: "submissive",
    });
    await seedGlobalProfile(owner, "sync-doc-legacy");
    const now = new Date().toISOString();
    await env.DB.prepare(
      `INSERT INTO domme_registrations
         (id, guild_id, creator_id, discord_user_id, active, profile_managed, created_at, updated_at)
       VALUES ('sync-legacy', ?, ?, ?, 1, 0, ?, ?)`,
    )
      .bind(HOME_GUILD, creator.id, owner, now, now)
      .run();

    await syncRegistrationForGuild(env, HOME_GUILD, owner);

    const row = await env.DB.prepare(
      "SELECT active, profile_managed FROM domme_registrations WHERE id = 'sync-legacy'",
    ).first<{ active: number; profile_managed: number }>();
    expect(row).toEqual({ active: 1, profile_managed: 0 });
  });
});
