import { env } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import {
  completeGuildSetupSession,
  createGuildSetupSession,
  setGuildSetupChannel,
} from "../src/profile/guildSetupService";
import { seedCreator, seedGuild } from "./helpers";
import { seedDocument, seedServerProfile } from "./profileHelpers";

const GUILD = "940000000000000001";
const INITIATOR = "940000000000000002";
const CHANNEL = "940000000000000003";

describe("guild setup sessions", () => {
  it("atomically materializes multiple safe profile registrations without replacing legacy rows", async () => {
    const owners = ["940000000000000010", "940000000000000011", "940000000000000012"];
    const creators = await Promise.all(
      owners.map((owner, index) =>
        seedCreator({
          id: `setup-creator-${index}`,
          ownerDiscordUserId: owner,
        }),
      ),
    );
    for (const [index, owner] of owners.entries()) {
      const documentId = `setup-document-${index}`;
      await seedDocument({
        id: documentId,
        ownerUserId: owner,
        orientation: "domme",
        throneCreatorId: creators[index]!.id,
      });
      await seedServerProfile({
        id: `setup-profile-${index}`,
        guildId: GUILD,
        ownerUserId: owner,
        mode: "independent",
        documentId,
      });
    }

    const now = new Date().toISOString();
    await seedGuild(GUILD, "940000000000000099");
    const legacyCreator = await seedCreator({
      id: "setup-legacy-creator",
      ownerDiscordUserId: owners[2] as string,
    });
    await env.DB.prepare(
      `INSERT INTO domme_registrations
         (id, guild_id, creator_id, discord_user_id, active, profile_managed, created_at, updated_at)
       VALUES ('setup-legacy', ?, ?, ?, 1, 0, ?, ?)`,
    )
      .bind(GUILD, legacyCreator.id, owners[2], now, now)
      .run();

    const created = await createGuildSetupSession(env, {
      guildId: GUILD,
      initiatorUserId: INITIATOR,
    });
    const selected = await setGuildSetupChannel(env, {
      sessionId: created.session.id,
      guildId: GUILD,
      initiatorUserId: INITIATOR,
      expectedRevision: created.session.revision,
      channelId: CHANNEL,
    });
    const completed = await completeGuildSetupSession(env, {
      sessionId: created.session.id,
      guildId: GUILD,
      initiatorUserId: INITIATOR,
      expectedRevision: selected.revision,
    });
    expect(completed.sendChannelId).toBe(CHANNEL);

    const { results } = await env.DB.prepare(
      `SELECT creator_id, discord_user_id, profile_managed
         FROM domme_registrations
        WHERE guild_id = ?
        ORDER BY discord_user_id`,
    )
      .bind(GUILD)
      .all<{ creator_id: string; discord_user_id: string; profile_managed: number }>();
    expect(results).toEqual([
      {
        creator_id: creators[0]?.id,
        discord_user_id: owners[0],
        profile_managed: 1,
      },
      {
        creator_id: creators[1]?.id,
        discord_user_id: owners[1],
        profile_managed: 1,
      },
      {
        creator_id: legacyCreator.id,
        discord_user_id: owners[2],
        profile_managed: 0,
      },
    ]);
  });
});
