import { describe, expect, it } from "vitest";
import { env } from "cloudflare:test";
import { authHeaders, callWorker, jsonRequest, readJson, TEST_HOME_GUILD_ID } from "./helpers";

const OWNER = "500000000000000001";
const OTHER_GUILD = "500000000000000002";
const OTHER_GUILD_2 = "500000000000000003";

interface DraftEnvelope {
  data: { resume_required?: boolean; draft: DraftBody };
}
interface DraftBody {
  id: string;
  revision: number;
  status: string;
  current_step: string;
  next_step: string | null;
  steps: { key: string; status: string }[];
  document: Record<string, unknown>;
}
interface ProfileEnvelope {
  data: { profile: Record<string, unknown> | null; global_available: boolean };
}

async function startDraft(body: Record<string, unknown>) {
  const response = await callWorker(jsonRequest("POST", "/v1/profile-drafts/start", body, authHeaders()));
  const parsed = await readJson<DraftEnvelope>(response);
  return { status: response.status, ...parsed.data };
}

async function getDraft(draftId: string, ownerUserId: string) {
  const response = await callWorker(
    jsonRequest("GET", `/v1/profile-drafts/${draftId}?owner_user_id=${ownerUserId}`, undefined, authHeaders()),
  );
  const parsed = await readJson<DraftEnvelope>(response);
  return { status: response.status, draft: parsed.data?.draft };
}

async function putStep(draftId: string, stepKey: string, body: Record<string, unknown>) {
  const response = await callWorker(
    jsonRequest("PUT", `/v1/profile-drafts/${draftId}/steps/${stepKey}`, body, authHeaders()),
  );
  const parsed = await readJson<DraftEnvelope & { error?: { code: string; message: string } }>(response);
  return { status: response.status, draft: parsed.data?.draft, error: (parsed as unknown as { error?: { code: string } }).error };
}

async function restart(draftId: string, body: Record<string, unknown>) {
  const response = await callWorker(jsonRequest("POST", `/v1/profile-drafts/${draftId}/restart`, body, authHeaders()));
  const parsed = await readJson<DraftEnvelope>(response);
  return { status: response.status, draft: parsed.data?.draft };
}

async function publish(draftId: string, body: Record<string, unknown>) {
  const response = await callWorker(jsonRequest("POST", `/v1/profile-drafts/${draftId}/publish`, body, authHeaders()));
  const parsed = await readJson<{ data?: { profile: Record<string, unknown> }; error?: { code: string } }>(response);
  return { status: response.status, profile: parsed.data?.profile, error: parsed.error };
}

async function lookup(guildId: string, userId: string) {
  const response = await callWorker(
    jsonRequest("GET", `/v1/guilds/${guildId}/profiles/${userId}`, undefined, authHeaders()),
  );
  const parsed = await readJson<ProfileEnvelope>(response);
  return { status: response.status, ...parsed.data };
}

describe("profile draft lifecycle (global scope, domme orientation)", () => {
  it("runs the full start -> steps -> publish -> lookup cycle", async () => {
    const started = await startDraft({
      owner_user_id: OWNER,
      origin_guild_id: TEST_HOME_GUILD_ID,
      target_scope: "global",
    });
    expect(started.status).toBe(200);
    expect(started.resume_required).toBe(false);
    const draftId = started.draft.id;
    expect(started.draft.revision).toBe(0);
    expect(started.draft.steps.map((s) => s.key)).toEqual(["orientation"]);

    const afterOrientation = await putStep(draftId, "orientation", {
      owner_user_id: OWNER,
      expected_revision: 0,
      orientation: "domme",
    });
    expect(afterOrientation.status).toBe(200);
    expect(afterOrientation.draft?.revision).toBe(1);
    expect(afterOrientation.draft?.steps.map((s) => s.key)).toEqual(["orientation", "identity", "links", "throne", "review"]);

    const afterIdentity = await putStep(draftId, "identity", {
      owner_user_id: OWNER,
      expected_revision: 1,
      pronouns: ["She/Her"],
      honourifics: ["Goddess"],
      dm_status: "open",
      bio: "Hello there",
      public_send_stats: false,
    });
    expect(afterIdentity.status).toBe(200);
    expect(afterIdentity.draft?.revision).toBe(2);

    const afterLinks = await putStep(draftId, "links", {
      owner_user_id: OWNER,
      expected_revision: 2,
      links: [
        {
          platform: "twitter",
          public_label: "Twitter",
          normalized_url: "https://twitter.com/example",
          link_type: "social",
        },
        {
          platform: "cashapp",
          public_label: "CashApp",
          normalized_url: "https://cash.app/$example",
          link_type: "payment",
        },
      ],
    });
    expect(afterLinks.status).toBe(200);
    expect(afterLinks.draft?.revision).toBe(3);
    const linkId = (afterLinks.draft?.document.links as { id: string; link_type: string }[]).find(
      (l) => l.link_type === "payment",
    )?.id;
    expect(linkId).toBeTruthy();

    const afterThrone = await putStep(draftId, "throne", {
      owner_user_id: OWNER,
      expected_revision: 3,
      throne_creator_id: null,
      preferred_payment_link_id: linkId,
    });
    expect(afterThrone.status).toBe(200);
    expect(afterThrone.draft?.revision).toBe(4);

    const afterReview = await putStep(draftId, "review", { owner_user_id: OWNER, expected_revision: 4 });
    expect(afterReview.status).toBe(200);
    expect(afterReview.draft?.steps.every((s) => s.status === "completed")).toBe(true);

    const published = await publish(draftId, { owner_user_id: OWNER, expected_revision: 5 });
    expect(published.status).toBe(200);
    expect(published.profile?.orientation).toBe("domme");
    expect(published.profile?.version).toBe(1);

    const looked = await lookup(TEST_HOME_GUILD_ID, OWNER);
    expect(looked.status).toBe(200);
    expect(looked.global_available).toBe(true);
    expect(looked.profile?.bio).toBe("Hello there");
    expect(looked.profile?.preferred_payment_link_id).toBe(linkId);

    // The draft is no longer active; further mutation must be refused.
    const stalePut = await putStep(draftId, "review", { owner_user_id: OWNER, expected_revision: 5 });
    expect(stalePut.status).toBe(409);
  });

  it("returns resume_required with the same draft when starting again while one is active", async () => {
    const owner = "500000000000000010";
    const first = await startDraft({ owner_user_id: owner, origin_guild_id: TEST_HOME_GUILD_ID, target_scope: "global" });
    expect(first.resume_required).toBe(false);

    const second = await startDraft({ owner_user_id: owner, origin_guild_id: TEST_HOME_GUILD_ID, target_scope: "global" });
    expect(second.status).toBe(200);
    expect(second.resume_required).toBe(true);
    expect(second.draft.id).toBe(first.draft.id);
  });

  it("rejects a global draft started outside the home guild", async () => {
    const response = await callWorker(
      jsonRequest(
        "POST",
        "/v1/profile-drafts/start",
        { owner_user_id: "500000000000000011", origin_guild_id: OTHER_GUILD, target_scope: "global" },
        authHeaders(),
      ),
    );
    expect(response.status).toBe(400);
    const body = await readJson<{ error: { code: string } }>(response);
    expect(body.error.code).toBe("home_guild_required");
  });

  it("rejects a server draft targeting the home guild", async () => {
    const response = await callWorker(
      jsonRequest(
        "POST",
        "/v1/profile-drafts/start",
        {
          owner_user_id: "500000000000000012",
          origin_guild_id: TEST_HOME_GUILD_ID,
          target_scope: "server",
          guild_id: TEST_HOME_GUILD_ID,
          server_mode: "independent",
        },
        authHeaders(),
      ),
    );
    expect(response.status).toBe(400);
    const body = await readJson<{ error: { code: string } }>(response);
    expect(body.error.code).toBe("server_scope_not_allowed_in_home_guild");
  });

  it("rejects mismatched expected_revision with 409 stale_revision", async () => {
    const owner = "500000000000000013";
    const started = await startDraft({ owner_user_id: owner, origin_guild_id: TEST_HOME_GUILD_ID, target_scope: "global" });
    const draftId = started.draft.id;

    const result = await putStep(draftId, "orientation", {
      owner_user_id: owner,
      expected_revision: 99,
      orientation: "domme",
    });
    expect(result.status).toBe(409);
    expect(result.error?.code).toBe("stale_revision");
  });

  it("submissive orientation has no throne step and rejects throne step mutation", async () => {
    const owner = "500000000000000014";
    const started = await startDraft({ owner_user_id: owner, origin_guild_id: TEST_HOME_GUILD_ID, target_scope: "global" });
    const draftId = started.draft.id;
    const afterOrientation = await putStep(draftId, "orientation", {
      owner_user_id: owner,
      expected_revision: 0,
      orientation: "submissive",
    });
    expect(afterOrientation.draft?.steps.map((s) => s.key)).toEqual(["orientation", "identity", "links", "review"]);

    const throneAttempt = await putStep(draftId, "throne", {
      owner_user_id: owner,
      expected_revision: 1,
      throne_creator_id: null,
      preferred_payment_link_id: null,
    });
    expect(throneAttempt.status).toBe(400);
    expect(throneAttempt.error?.code).toBe("step_not_applicable");
  });

  it("restart resets step completion and document content back to the published baseline", async () => {
    const owner = "500000000000000015";
    const started = await startDraft({ owner_user_id: owner, origin_guild_id: TEST_HOME_GUILD_ID, target_scope: "global" });
    const draftId = started.draft.id;
    const afterOrientation = await putStep(draftId, "orientation", {
      owner_user_id: owner,
      expected_revision: 0,
      orientation: "domme",
    });
    expect(afterOrientation.draft?.current_step).toBe("orientation");

    const restarted = await restart(draftId, { owner_user_id: owner, expected_revision: 1 });
    expect(restarted.status).toBe(200);
    expect(restarted.draft?.revision).toBe(2);
    expect(restarted.draft?.current_step).toBe("orientation");
    expect(restarted.draft?.steps.every((s) => s.status === "pending")).toBe(true);
    expect((restarted.draft?.document as { orientation?: unknown } | undefined)?.orientation).toBeUndefined();

    const reread = await getDraft(draftId, owner);
    expect(reread.draft?.document.selections).toEqual({ pronouns: [], honourifics: [], submissive_labels: [] });
  });

  it("rejects publish when required steps are incomplete", async () => {
    const owner = "500000000000000016";
    const started = await startDraft({ owner_user_id: owner, origin_guild_id: TEST_HOME_GUILD_ID, target_scope: "global" });
    const draftId = started.draft.id;
    await putStep(draftId, "orientation", { owner_user_id: owner, expected_revision: 0, orientation: "domme" });

    const result = await publish(draftId, { owner_user_id: owner, expected_revision: 1 });
    expect(result.status).toBe(400);
    expect(result.error?.code).toBe("steps_incomplete");
  });

  it("detects a version conflict at publish time with a clear 409", async () => {
    const owner = "500000000000000017";
    async function completeDraft(): Promise<string> {
      const started = await startDraft({ owner_user_id: owner, origin_guild_id: TEST_HOME_GUILD_ID, target_scope: "global" });
      const draftId = started.draft.id;
      await putStep(draftId, "orientation", { owner_user_id: owner, expected_revision: 0, orientation: "domme" });
      await putStep(draftId, "identity", {
        owner_user_id: owner,
        expected_revision: 1,
        dm_status: "open",
      });
      await putStep(draftId, "links", { owner_user_id: owner, expected_revision: 2, links: [] });
      await putStep(draftId, "throne", {
        owner_user_id: owner,
        expected_revision: 3,
        throne_creator_id: null,
        preferred_payment_link_id: null,
      });
      await putStep(draftId, "review", { owner_user_id: owner, expected_revision: 4 });
      return draftId;
    }

    const draftId = await completeDraft();

    // Two concurrent publish attempts for the very same completed draft: only one
    // may ever win the compare-and-swap on the root's version, and the batch's
    // EXISTS-guarded statements ensure the loser leaves no partial trace (no
    // second publication row, no document flipped to `published` twice).
    const [first, second] = await Promise.all([
      publish(draftId, { owner_user_id: owner, expected_revision: 5 }),
      publish(draftId, { owner_user_id: owner, expected_revision: 5 }),
    ]);
    const statuses = [first.status, second.status].sort();
    expect(statuses).toEqual([200, 409]);
    const loser = first.status === 409 ? first : second;
    expect(["publish_conflict", "stale_revision", "draft_not_active"]).toContain(loser.error?.code);

    const publicationCount = await env.DB.prepare(
      "SELECT COUNT(*) as count FROM profile_publications WHERE owner_user_id = ?",
    )
      .bind(owner)
      .first<{ count: number }>();
    expect(publicationCount?.count).toBe(1);
  });
});

describe("profile draft lifecycle (server scope)", () => {
  it("publishes an independent server profile distinct from any global profile", async () => {
    const owner = "500000000000000020";
    const started = await startDraft({
      owner_user_id: owner,
      origin_guild_id: OTHER_GUILD,
      target_scope: "server",
      guild_id: OTHER_GUILD,
      server_mode: "independent",
    });
    const draftId = started.draft.id;
    await putStep(draftId, "orientation", { owner_user_id: owner, expected_revision: 0, orientation: "switch_domme" });
    await putStep(draftId, "identity", {
      owner_user_id: owner,
      expected_revision: 1,
      pronouns: ["He/Him"],
      honourifics: ["Master"],
      submissive_labels: ["Pet"],
      dm_status: "by_request",
      aliases: ["Buddy"],
      public_send_stats: true,
    });
    await putStep(draftId, "links", { owner_user_id: owner, expected_revision: 2, links: [] });
    await putStep(draftId, "throne", {
      owner_user_id: owner,
      expected_revision: 3,
      throne_creator_id: null,
      preferred_payment_link_id: null,
    });
    await putStep(draftId, "review", { owner_user_id: owner, expected_revision: 4 });

    const published = await publish(draftId, { owner_user_id: owner, expected_revision: 5 });
    expect(published.status).toBe(200);
    expect(published.profile?.scope).toBe("server");
    expect(published.profile?.mode).toBe("independent");

    const looked = await lookup(OTHER_GUILD, owner);
    expect(looked.profile?.mode).toBe("independent");
    expect(looked.global_available).toBe(false);
  });

  it("publishes a linked overlay that overrides one field and adds a local link", async () => {
    const owner = "500000000000000021";
    // Publish a global profile first.
    const globalStart = await startDraft({ owner_user_id: owner, origin_guild_id: TEST_HOME_GUILD_ID, target_scope: "global" });
    const globalDraftId = globalStart.draft.id;
    await putStep(globalDraftId, "orientation", { owner_user_id: owner, expected_revision: 0, orientation: "domme" });
    await putStep(globalDraftId, "identity", {
      owner_user_id: owner,
      expected_revision: 1,
      pronouns: ["She/Her"],
      dm_status: "open",
      bio: "global bio",
    });
    await putStep(globalDraftId, "links", {
      owner_user_id: owner,
      expected_revision: 2,
      links: [
        { platform: "cashapp", public_label: "CashApp", normalized_url: "https://cash.app/$owner21", link_type: "payment" },
      ],
    });
    await putStep(globalDraftId, "throne", {
      owner_user_id: owner,
      expected_revision: 3,
      throne_creator_id: null,
      preferred_payment_link_id: null,
    });
    await putStep(globalDraftId, "review", { owner_user_id: owner, expected_revision: 4 });
    await publish(globalDraftId, { owner_user_id: owner, expected_revision: 5 });

    // Now start a linked draft in a different guild.
    const linkedStart = await startDraft({
      owner_user_id: owner,
      origin_guild_id: OTHER_GUILD_2,
      target_scope: "server",
      guild_id: OTHER_GUILD_2,
      server_mode: "linked",
    });
    expect(linkedStart.draft.steps.map((s: { key: string }) => s.key)).toEqual(["identity", "links", "review"]);
    const linkedDraftId = linkedStart.draft.id;

    const afterIdentity = await putStep(linkedDraftId, "identity", {
      owner_user_id: owner,
      expected_revision: 0,
      overrides: ["dm_status"],
      dm_status: "closed",
    });
    expect(afterIdentity.status).toBe(200);

    const afterLinks = await putStep(linkedDraftId, "links", {
      owner_user_id: owner,
      expected_revision: 1,
      local_links: [
        { platform: "onlyfans", public_label: "Local Only", normalized_url: "https://example.com/local21", link_type: "social" },
      ],
      hidden_inherited_link_ids: [],
      preferred_payment_link_id: null,
    });
    expect(afterLinks.status).toBe(200);

    await putStep(linkedDraftId, "review", { owner_user_id: owner, expected_revision: 2 });
    const published = await publish(linkedDraftId, { owner_user_id: owner, expected_revision: 3 });
    expect(published.status).toBe(200);
    expect(published.profile?.mode).toBe("linked");
    expect(published.profile?.dm_status).toBe("closed");
    expect(published.profile?.bio).toBe("global bio");

    const looked = await lookup(OTHER_GUILD_2, owner);
    expect(looked.profile?.dm_status).toBe("closed");
    expect(looked.profile?.bio).toBe("global bio");
    const links = looked.profile?.links as { platform: string }[];
    expect(links.some((l) => l.platform === "onlyfans")).toBe(true);
    expect(links.some((l) => l.platform === "cashapp")).toBe(true);
  });
});
