import { env } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";
import {
  authHeaders,
  callWorker,
  generateThroneKeyPair,
  jsonRequest,
  readJson,
  seedCreator,
  TEST_HOME_GUILD_ID,
} from "./helpers";
import { seedDocument, seedGlobalProfile } from "./profileHelpers";

const LINKED_GUILD = "750000000000000001";

interface KeyPair {
  publicKeyPem: string;
  sign: (timestamp: string, rawBody: string) => Promise<string>;
}

let keyPair: KeyPair;

beforeEach(async () => {
  keyPair = await generateThroneKeyPair();
  env.THRONE_PUBLIC_KEY_PEM = keyPair.publicKeyPem;
});

interface DraftBody {
  id: string;
  revision: number;
}
interface DraftEnvelope {
  data?: { draft: DraftBody };
  error?: { code: string };
}
interface StatusEnvelope {
  data?: Record<string, unknown>;
  error?: { code: string; message: string };
}

async function startDraft(body: Record<string, unknown>): Promise<DraftBody> {
  const response = await callWorker(jsonRequest("POST", "/v1/profile-drafts/start", body, authHeaders()));
  return (await readJson<DraftEnvelope>(response)).data!.draft;
}

async function putStep(draftId: string, stepKey: string, body: Record<string, unknown>): Promise<DraftBody> {
  const response = await callWorker(
    jsonRequest("PUT", `/v1/profile-drafts/${draftId}/steps/${stepKey}`, body, authHeaders()),
  );
  return (await readJson<DraftEnvelope>(response)).data!.draft;
}

async function startDommeDraft(owner: string): Promise<DraftBody> {
  const draft = await startDraft({
    owner_user_id: owner,
    origin_guild_id: TEST_HOME_GUILD_ID,
    target_scope: "global",
  });
  return putStep(draft.id, "orientation", {
    owner_user_id: owner,
    expected_revision: draft.revision,
    orientation: "domme",
  });
}

async function attachCreator(
  draft: DraftBody,
  owner: string,
  creatorId: string,
): Promise<{ id: string; revision: number; webhookUrl: string }> {
  const response = await callWorker(
    jsonRequest(
      "POST",
      `/v1/profile-drafts/${draft.id}/throne`,
      {
        owner_user_id: owner,
        expected_revision: draft.revision,
        existing_creator_id: creatorId,
        rotate_webhook: true,
      },
      authHeaders(),
    ),
  );
  const parsed = await readJson<{ data: { draft: DraftBody; webhook_url: string } }>(response);
  expect(response.status).toBe(200);
  return { id: draft.id, revision: parsed.data.draft.revision, webhookUrl: parsed.data.webhook_url };
}

async function getStatus(draftId: string, query: string, headers = authHeaders()) {
  const response = await callWorker(
    jsonRequest("GET", `/v1/profile-drafts/${draftId}/throne/status?${query}`, undefined, headers),
  );
  const parsed = await readJson<StatusEnvelope>(response);
  return { status: response.status, body: parsed.data, error: parsed.error };
}

async function postWebhook(creatorId: string, secret: string, body: unknown): Promise<Response> {
  const rawBody = JSON.stringify(body);
  const timestamp = String(Math.floor(Date.now() / 1000));
  return callWorker(
    new Request(`https://worker.test/t/${creatorId}/${secret}`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "X-Signature-Timestamp": timestamp,
        "X-Signature-Ed25519": await keyPair.sign(timestamp, rawBody),
      },
      body: rawBody,
    }),
  );
}

describe("GET /v1/profile-drafts/:draftId/throne/status", () => {
  it("reports an unverified connection, then flips to verified after Throne's signed test webhook", async () => {
    const owner = "750000000000000010";
    const draft = await startDommeDraft(owner);
    const creator = await seedCreator({
      id: "throne-status-creator",
      handle: "statusqueen",
      ownerDiscordUserId: owner,
      secret: "status-secret",
    });
    const attached = await attachCreator(draft, owner, creator.id);

    const before = await getStatus(attached.id, `owner_user_id=${owner}&expected_revision=${attached.revision}`);
    expect(before.status).toBe(200);
    expect(before.body).toEqual({ handle: "statusqueen", verified: false, verified_at: null });

    // Only Throne itself, through the signed public webhook route, can flip verification.
    const secret = attached.webhookUrl.split("/").at(-1) as string;
    const webhookResponse = await postWebhook(creator.id, secret, {
      type: "gift_purchased",
      test: true,
      amount: 5,
    });
    expect(webhookResponse.status).toBe(200);

    const after = await getStatus(attached.id, `owner_user_id=${owner}&expected_revision=${attached.revision}`);
    expect(after.status).toBe(200);
    expect(after.body?.handle).toBe("statusqueen");
    expect(after.body?.verified).toBe(true);
    expect(typeof after.body?.verified_at).toBe("string");
  });

  it("never leaks the creator id, route secret, or webhook URL", async () => {
    const owner = "750000000000000011";
    const draft = await startDommeDraft(owner);
    const creator = await seedCreator({
      id: "throne-status-private",
      handle: "privateer",
      ownerDiscordUserId: owner,
      secret: "super-secret-value",
    });
    const attached = await attachCreator(draft, owner, creator.id);

    const status = await getStatus(attached.id, `owner_user_id=${owner}&expected_revision=${attached.revision}`);
    expect(Object.keys(status.body ?? {}).sort()).toEqual(["handle", "verified", "verified_at"]);
    const serialized = JSON.stringify(status.body);
    expect(serialized).not.toContain(creator.id);
    expect(serialized).not.toContain("super-secret-value");
    expect(serialized).not.toContain("public-");
    expect(serialized).not.toContain("/t/");

    const row = await env.DB.prepare("SELECT route_secret_hash, public_creator_id FROM throne_creators WHERE id = ?")
      .bind(creator.id)
      .first<{ route_secret_hash: string; public_creator_id: string }>();
    expect(serialized).not.toContain(row?.route_secret_hash as string);
    expect(serialized).not.toContain(row?.public_creator_id as string);
  });

  it("reports a not-yet-connected draft as unverified with no handle", async () => {
    const owner = "750000000000000012";
    const draft = await startDommeDraft(owner);

    const status = await getStatus(draft.id, `owner_user_id=${owner}&expected_revision=${draft.revision}`);
    expect(status.status).toBe(200);
    expect(status.body).toEqual({ handle: null, verified: false, verified_at: null });
  });

  it("hides a creator the draft's owner does not own", async () => {
    const owner = "750000000000000013";
    const draft = await startDommeDraft(owner);
    await seedCreator({ id: "throne-status-foreign", handle: "someoneelse", ownerDiscordUserId: "750000000000000014" });
    await env.DB.prepare(
      "UPDATE profile_documents SET throne_creator_id = ? WHERE id = (SELECT document_id FROM profile_drafts WHERE id = ?)",
    )
      .bind("throne-status-foreign", draft.id)
      .run();

    const status = await getStatus(draft.id, `owner_user_id=${owner}&expected_revision=${draft.revision}`);
    expect(status.status).toBe(200);
    expect(status.body).toEqual({ handle: null, verified: false, verified_at: null });
  });

  it("requires bearer auth", async () => {
    const owner = "750000000000000015";
    const draft = await startDommeDraft(owner);
    const status = await getStatus(draft.id, `owner_user_id=${owner}&expected_revision=${draft.revision}`, {});
    expect(status.status).toBe(401);
    expect(status.body).toBeUndefined();
  });

  it("validates the owner, the draft, and the exact revision", async () => {
    const owner = "750000000000000016";
    const draft = await startDommeDraft(owner);

    const wrongOwner = await getStatus(
      draft.id,
      `owner_user_id=750000000000000017&expected_revision=${draft.revision}`,
    );
    expect(wrongOwner.status).toBe(404);
    expect(wrongOwner.error?.code).toBe("draft_not_found");

    const badOwner = await getStatus(draft.id, `owner_user_id=nope&expected_revision=${draft.revision}`);
    expect(badOwner.status).toBe(400);
    expect(badOwner.error?.code).toBe("invalid_owner_user_id");

    const missingRevision = await getStatus(draft.id, `owner_user_id=${owner}`);
    expect(missingRevision.status).toBe(400);
    expect(missingRevision.error?.code).toBe("invalid_expected_revision");

    const malformedRevision = await getStatus(draft.id, `owner_user_id=${owner}&expected_revision=-1`);
    expect(malformedRevision.status).toBe(400);
    expect(malformedRevision.error?.code).toBe("invalid_expected_revision");

    const staleRevision = await getStatus(
      draft.id,
      `owner_user_id=${owner}&expected_revision=${draft.revision + 1}`,
    );
    expect(staleRevision.status).toBe(409);
    expect(staleRevision.error?.code).toBe("stale_revision");

    const unknownDraft = await getStatus("no-such-draft", `owner_user_id=${owner}&expected_revision=0`);
    expect(unknownDraft.status).toBe(404);
    expect(unknownDraft.error?.code).toBe("draft_not_found");
  });

  it("refuses orientations and draft shapes that have no Throne step", async () => {
    const submissiveOwner = "750000000000000018";
    const submissiveDraft = await startDraft({
      owner_user_id: submissiveOwner,
      origin_guild_id: TEST_HOME_GUILD_ID,
      target_scope: "global",
    });
    const oriented = await putStep(submissiveDraft.id, "orientation", {
      owner_user_id: submissiveOwner,
      expected_revision: submissiveDraft.revision,
      orientation: "submissive",
    });
    const submissiveStatus = await getStatus(
      oriented.id,
      `owner_user_id=${submissiveOwner}&expected_revision=${oriented.revision}`,
    );
    expect(submissiveStatus.status).toBe(400);
    expect(submissiveStatus.error?.code).toBe("throne_unavailable");

    const linkedOwner = "750000000000000019";
    await seedDocument({ id: "throne-status-global", ownerUserId: linkedOwner, orientation: "domme", dmStatus: "open" });
    await seedGlobalProfile(linkedOwner, "throne-status-global");
    const linkedDraft = await startDraft({
      owner_user_id: linkedOwner,
      origin_guild_id: TEST_HOME_GUILD_ID,
      target_scope: "server",
      guild_id: LINKED_GUILD,
      server_mode: "linked",
    });
    const linkedStatus = await getStatus(
      linkedDraft.id,
      `owner_user_id=${linkedOwner}&expected_revision=${linkedDraft.revision}`,
    );
    expect(linkedStatus.status).toBe(400);
    expect(linkedStatus.error?.code).toBe("step_not_applicable");
  });

  it("is a read: it never advances the draft revision or mutates the creator row", async () => {
    const owner = "750000000000000020";
    const draft = await startDommeDraft(owner);
    const creator = await seedCreator({
      id: "throne-status-readonly",
      handle: "readonly",
      ownerDiscordUserId: owner,
    });
    const attached = await attachCreator(draft, owner, creator.id);

    const creatorBefore = await env.DB.prepare("SELECT * FROM throne_creators WHERE id = ?")
      .bind(creator.id)
      .first();
    await getStatus(attached.id, `owner_user_id=${owner}&expected_revision=${attached.revision}`);
    await getStatus(attached.id, `owner_user_id=${owner}&expected_revision=${attached.revision}`);

    const draftRow = await env.DB.prepare("SELECT revision FROM profile_drafts WHERE id = ?")
      .bind(attached.id)
      .first<{ revision: number }>();
    expect(draftRow?.revision).toBe(attached.revision);
    expect(await env.DB.prepare("SELECT * FROM throne_creators WHERE id = ?").bind(creator.id).first()).toEqual(
      creatorBefore,
    );
  });
});
