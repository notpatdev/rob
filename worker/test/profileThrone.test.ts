import { env } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import { attachThroneToDraft } from "../src/profile/throneDraftService";
import { sha256Hex } from "../src/util/hash";
import { authHeaders, callWorker, jsonRequest, readJson, seedCreator, TEST_HOME_GUILD_ID } from "./helpers";

interface DraftEnvelope {
  data: { draft: { id: string; revision: number } };
}

async function startDommeDraft(owner: string): Promise<{ id: string; revision: number }> {
  const startedResponse = await callWorker(
    jsonRequest(
      "POST",
      "/v1/profile-drafts/start",
      {
        owner_user_id: owner,
        origin_guild_id: TEST_HOME_GUILD_ID,
        target_scope: "global",
      },
      authHeaders(),
    ),
  );
  const started = await readJson<DraftEnvelope>(startedResponse);
  const orientationResponse = await callWorker(
    jsonRequest(
      "PUT",
      `/v1/profile-drafts/${started.data.draft.id}/steps/orientation`,
      {
        owner_user_id: owner,
        expected_revision: 0,
        orientation: "domme",
      },
      authHeaders(),
    ),
  );
  const oriented = await readJson<DraftEnvelope>(orientationResponse);
  return oriented.data.draft;
}

describe("profile Throne mutations", () => {
  it("lets only one racing rotation change the live webhook secret", async () => {
    const owner = "930000000000000001";
    const draft = await startDommeDraft(owner);
    const creator = await seedCreator({
      id: "profile-throne-race",
      ownerDiscordUserId: owner,
      secret: "original-secret",
    });

    const attempts = await Promise.allSettled([
      attachThroneToDraft(env, {
        draftId: draft.id,
        ownerUserId: owner,
        expectedRevision: draft.revision,
        throneInput: null,
        existingCreatorId: creator.id,
        rotateWebhook: true,
      }),
      attachThroneToDraft(env, {
        draftId: draft.id,
        ownerUserId: owner,
        expectedRevision: draft.revision,
        throneInput: null,
        existingCreatorId: creator.id,
        rotateWebhook: true,
      }),
    ]);
    const fulfilled = attempts.filter(
      (attempt): attempt is PromiseFulfilledResult<Awaited<ReturnType<typeof attachThroneToDraft>>> =>
        attempt.status === "fulfilled",
    );
    const rejected = attempts.filter(
      (attempt): attempt is PromiseRejectedResult => attempt.status === "rejected",
    );
    expect(fulfilled).toHaveLength(1);
    expect(rejected).toHaveLength(1);
    expect(rejected[0]?.reason).toEqual(expect.objectContaining({ code: "stale_revision" }));

    const webhookUrl = fulfilled[0]?.value.webhookUrl;
    expect(webhookUrl).toBeTruthy();
    const secret = webhookUrl?.split("/").at(-1);
    const row = await env.DB.prepare(
      "SELECT route_secret_hash FROM throne_creators WHERE id = ?",
    )
      .bind(creator.id)
      .first<{ route_secret_hash: string }>();
    expect(row?.route_secret_hash).toBe(await sha256Hex(secret as string));
    expect(row?.route_secret_hash).not.toBe(await sha256Hex("original-secret"));
  });
});
