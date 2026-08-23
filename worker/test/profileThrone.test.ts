import { env, fetchMock } from "cloudflare:test";
import { beforeAll, describe, expect, it } from "vitest";
import { attachThroneToDraft } from "../src/profile/throneDraftService";
import { sha256Hex } from "../src/util/hash";
import { authHeaders, callWorker, jsonRequest, readJson, seedCreator, TEST_HOME_GUILD_ID } from "./helpers";

interface DraftEnvelope {
  data: { draft: { id: string; revision: number } };
}

const FIRESTORE_PATH = "/v1/projects/onlywish-9d17b/databases/(default)/documents:runQuery";

beforeAll(() => {
  fetchMock.activate();
  fetchMock.disableNetConnect();
});

function mockResolve(publicCreatorId: string, handle: string): void {
  fetchMock
    .get("https://firestore.googleapis.com")
    .intercept({ method: "POST", path: FIRESTORE_PATH })
    .reply(200, [
      {
        document: {
          name: `projects/onlywish-9d17b/databases/(default)/documents/creators/${publicCreatorId}`,
          fields: { username: { stringValue: handle } },
        },
      },
    ]);
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
  it("resolves without issuing a secret, then confirms restart-safely from the draft", async () => {
    const owner = "930000000000000010";
    const draft = await startDommeDraft(owner);
    mockResolve("public-confirmed-creator", "confirmedqueen");

    const resolvedResponse = await callWorker(
      jsonRequest(
        "POST",
        `/v1/profile-drafts/${draft.id}/throne/resolve`,
        {
          owner_user_id: owner,
          expected_revision: draft.revision,
          throne_input: "confirmedqueen",
        },
        authHeaders(),
      ),
    );
    expect(resolvedResponse.status).toBe(200);
    const resolved = await readJson<{
      data: {
        draft: { revision: number; throne_pending: { handle: string } };
        handle: string;
        already_verified: boolean;
        confirmation_token: string;
      };
    }>(resolvedResponse);
    expect(resolved.data.handle).toBe("confirmedqueen");
    expect(resolved.data.draft.throne_pending.handle).toBe("confirmedqueen");
    expect(resolved.data.already_verified).toBe(false);
    expect(resolved.data.confirmation_token).toBeTruthy();
    expect(
      await env.DB.prepare("SELECT id FROM throne_creators WHERE public_creator_id = ?")
        .bind("public-confirmed-creator")
        .first(),
    ).toBeNull();

    const resumedResponse = await callWorker(
      new Request(
        `https://worker.test/v1/profile-drafts/${draft.id}?owner_user_id=${owner}`,
        { headers: authHeaders() },
      ),
    );
    expect(resumedResponse.status).toBe(200);
    const resumed = await readJson<{
      data: { draft: { throne_pending: { handle: string }; revision: number } };
    }>(resumedResponse);
    expect(resumed.data.draft.throne_pending.handle).toBe("confirmedqueen");

    const confirmResponse = await callWorker(
      jsonRequest(
        "POST",
        `/v1/profile-drafts/${draft.id}/throne`,
        {
          owner_user_id: owner,
          expected_revision: resumed.data.draft.revision,
          confirm_pending: true,
        },
        authHeaders(),
      ),
    );
    expect(confirmResponse.status).toBe(200);
    const confirmed = await readJson<{
      data: { webhook_url: string; draft: { throne_pending: null } };
    }>(confirmResponse);
    expect(confirmed.data.webhook_url).toMatch(/^https:\/\/usebill\.dev\/t\/[^/]+\/[\w-]+$/);
    expect(confirmed.data.draft.throne_pending).toBeNull();
    expect(
      await env.DB.prepare("SELECT owner_discord_user_id FROM throne_creators WHERE public_creator_id = ?")
        .bind("public-confirmed-creator")
        .first(),
    ).toEqual({ owner_discord_user_id: owner });
  });

  it("requires live webhook verification when completing Throne and publishing", async () => {
    const owner = "930000000000000011";
    const draft = await startDommeDraft(owner);
    const creator = await seedCreator({
      id: "profile-throne-live-verification",
      ownerDiscordUserId: owner,
    });
    const attached = await attachThroneToDraft(env, {
      draftId: draft.id,
      ownerUserId: owner,
      expectedRevision: draft.revision,
      throneInput: null,
      existingCreatorId: creator.id,
      confirmationToken: null,
      rotateWebhook: false,
    });

    async function putStep(
      step: string,
      expectedRevision: number,
      values: Record<string, unknown>,
    ): Promise<{ status: number; revision?: number; code?: string }> {
      const response = await callWorker(
        jsonRequest(
          "PUT",
          `/v1/profile-drafts/${draft.id}/steps/${step}`,
          { owner_user_id: owner, expected_revision: expectedRevision, ...values },
          authHeaders(),
        ),
      );
      const body = await readJson<{
        data?: { draft: { revision: number } };
        error?: { code: string };
      }>(response);
      return {
        status: response.status,
        ...(body.data === undefined ? {} : { revision: body.data.draft.revision }),
        ...(body.error === undefined ? {} : { code: body.error.code }),
      };
    }

    const unverified = await putStep("throne", attached.draft.revision, {
      throne_creator_id: creator.id,
      preferred_payment_link_id: null,
    });
    expect(unverified).toMatchObject({
      status: 400,
      code: "throne_webhook_unverified",
    });

    await env.DB.prepare("UPDATE throne_creators SET webhook_verified_at = ? WHERE id = ?")
      .bind("2026-08-23T12:00:00Z", creator.id)
      .run();
    const identity = await putStep("identity", attached.draft.revision, {
      pronouns: ["She/Her"],
      honourifics: [],
      submissive_labels: [],
      dm_status: "open",
      bio: null,
      public_send_stats: false,
      aliases: [],
      profile_color: null,
    });
    const links = await putStep("links", identity.revision as number, { links: [] });
    const throne = await putStep("throne", links.revision as number, {
      throne_creator_id: creator.id,
      preferred_payment_link_id: null,
    });
    const review = await putStep("review", throne.revision as number, {});

    await env.DB.prepare("UPDATE throne_creators SET webhook_verified_at = NULL WHERE id = ?")
      .bind(creator.id)
      .run();
    const publishResponse = await callWorker(
      jsonRequest(
        "POST",
        `/v1/profile-drafts/${draft.id}/publish`,
        { owner_user_id: owner, expected_revision: review.revision },
        authHeaders(),
      ),
    );
    expect(publishResponse.status).toBe(400);
    expect(
      (await readJson<{ error: { code: string } }>(publishResponse)).error.code,
    ).toBe("throne_webhook_unverified");
  });

  it("lets only one racing rotation change the live webhook secret", async () => {
    const owner = "930000000000000001";
    const draft = await startDommeDraft(owner);
    const creator = await seedCreator({
      id: "profile-throne-race",
      ownerDiscordUserId: owner,
      secret: "original-secret",
    });
    await env.DB.prepare("UPDATE throne_creators SET webhook_verified_at = ? WHERE id = ?")
      .bind("2026-08-23T12:00:00Z", creator.id)
      .run();

    const attempts = await Promise.allSettled([
      attachThroneToDraft(env, {
        draftId: draft.id,
        ownerUserId: owner,
        expectedRevision: draft.revision,
        throneInput: null,
        existingCreatorId: creator.id,
        confirmationToken: null,
        rotateWebhook: true,
      }),
      attachThroneToDraft(env, {
        draftId: draft.id,
        ownerUserId: owner,
        expectedRevision: draft.revision,
        throneInput: null,
        existingCreatorId: creator.id,
        confirmationToken: null,
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
      "SELECT route_secret_hash, webhook_verified_at FROM throne_creators WHERE id = ?",
    )
      .bind(creator.id)
      .first<{ route_secret_hash: string; webhook_verified_at: string | null }>();
    expect(row?.route_secret_hash).toBe(await sha256Hex(secret as string));
    expect(row?.route_secret_hash).not.toBe(await sha256Hex("original-secret"));
    expect(row?.webhook_verified_at).toBeNull();
  });
});
