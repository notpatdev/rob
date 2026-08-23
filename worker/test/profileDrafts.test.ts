import { describe, expect, it } from "vitest";
import { env } from "cloudflare:test";
import { authHeaders, callWorker, jsonRequest, readJson, TEST_HOME_GUILD_ID } from "./helpers";
import { seedDocument, seedGlobalProfile, seedSelection } from "./profileHelpers";

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
  dm_status_selected: boolean;
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
  it.each([
    {
      owner: "500000000000000191",
      target_scope: "global",
      origin_guild_id: TEST_HOME_GUILD_ID,
    },
    {
      owner: "500000000000000192",
      target_scope: "server",
      origin_guild_id: OTHER_GUILD,
      guild_id: OTHER_GUILD,
      server_mode: "independent",
    },
  ])("rejects completing a $target_scope identity without pronouns", async (scope) => {
    const started = await startDraft({
      owner_user_id: scope.owner,
      origin_guild_id: scope.origin_guild_id,
      target_scope: scope.target_scope,
      ...(scope.guild_id === undefined
        ? {}
        : { guild_id: scope.guild_id, server_mode: scope.server_mode }),
    });
    await putStep(started.draft.id, "orientation", {
      owner_user_id: scope.owner,
      expected_revision: 0,
      orientation: "domme",
    });

    const result = await putStep(started.draft.id, "identity", {
      owner_user_id: scope.owner,
      expected_revision: 1,
      pronouns: [],
      dm_status: "open",
      dm_status_selected: true,
    });

    expect(result.status).toBe(400);
    expect(result.error?.code).toBe("pronouns_required");
  });

  it("defends publication when a completed global draft has no pronouns", async () => {
    const owner = "500000000000000193";
    const started = await startDraft({
      owner_user_id: owner,
      origin_guild_id: TEST_HOME_GUILD_ID,
      target_scope: "global",
    });
    const draftId = started.draft.id;
    await putStep(draftId, "orientation", {
      owner_user_id: owner,
      expected_revision: 0,
      orientation: "domme",
    });
    await putStep(draftId, "identity", {
      owner_user_id: owner,
      expected_revision: 1,
      pronouns: ["She/Her"],
      dm_status: "open",
      dm_status_selected: true,
    });
    await putStep(draftId, "links", {
      owner_user_id: owner,
      expected_revision: 2,
      links: [],
    });
    await putStep(draftId, "throne", {
      owner_user_id: owner,
      expected_revision: 3,
      throne_creator_id: null,
      preferred_payment_link_id: null,
    });
    await putStep(draftId, "review", {
      owner_user_id: owner,
      expected_revision: 4,
    });
    await env.DB.prepare(
      `DELETE FROM profile_document_selections
        WHERE document_id = (SELECT document_id FROM profile_drafts WHERE id = ?)
          AND category = 'pronoun'`,
    )
      .bind(draftId)
      .run();

    const result = await publish(draftId, {
      owner_user_id: owner,
      expected_revision: 5,
    });

    expect(result.status).toBe(400);
    expect(result.error?.code).toBe("pronouns_required");
  });

  it("persists partial identity selections without choosing a DM status or completing the step", async () => {
    const owner = "500000000000000090";
    const started = await startDraft({
      owner_user_id: owner,
      origin_guild_id: TEST_HOME_GUILD_ID,
      target_scope: "global",
    });
    const draftId = started.draft.id;
    await putStep(draftId, "orientation", {
      owner_user_id: owner,
      expected_revision: 0,
      orientation: "domme",
    });

    const partial = await putStep(draftId, "identity", {
      owner_user_id: owner,
      expected_revision: 1,
      complete: false,
      pronouns: ["She/Her"],
      honourifics: [],
      submissive_labels: [],
      dm_status: null,
      bio: null,
      public_send_stats: false,
      aliases: [],
    });
    expect(partial.status).toBe(200);
    expect(partial.draft?.revision).toBe(2);
    expect(partial.draft?.next_step).toBe("identity");
    expect(partial.draft?.steps.find((step) => step.key === "identity")?.status).toBe("pending");
    expect(partial.draft?.document.selections).toEqual({
      pronouns: ["She/Her"],
      honourifics: [],
      submissive_labels: [],
    });
    expect(partial.draft?.document.dm_status).toBeNull();

    const withDmStatus = await putStep(draftId, "identity", {
      owner_user_id: owner,
      expected_revision: 2,
      complete: false,
      pronouns: ["She/Her"],
      honourifics: [],
      submissive_labels: [],
      dm_status: "by_request",
      dm_status_selected: true,
      bio: null,
      public_send_stats: false,
      aliases: [],
    });
    expect(withDmStatus.status).toBe(200);
    expect(withDmStatus.draft?.next_step).toBe("identity");
    expect(withDmStatus.draft?.dm_status_selected).toBe(true);
    expect(withDmStatus.draft?.document.dm_status).toBe("by_request");
    expect(withDmStatus.draft?.document.selections).toEqual({
      pronouns: ["She/Her"],
      honourifics: [],
      submissive_labels: [],
    });

    const resumed = await startDraft({
      owner_user_id: owner,
      origin_guild_id: TEST_HOME_GUILD_ID,
      target_scope: "global",
    });
    expect(resumed.resume_required).toBe(true);
    expect(resumed.draft.id).toBe(draftId);
    expect(resumed.draft.dm_status_selected).toBe(true);
    expect(resumed.draft.document.dm_status).toBe("by_request");

    const completed = await putStep(draftId, "identity", {
      owner_user_id: owner,
      expected_revision: 3,
      pronouns: ["She/Her"],
      honourifics: [],
      submissive_labels: [],
      dm_status: "by_request",
      bio: null,
      public_send_stats: false,
      aliases: [],
    });
    expect(completed.draft?.next_step).toBe("links");
  });

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
      dm_status_selected: true,
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

    const edit = await startDraft({
      owner_user_id: OWNER,
      origin_guild_id: TEST_HOME_GUILD_ID,
      target_scope: "global",
    });
    expect(edit.status).toBe(200);
    expect(edit.resume_required).toBe(false);
    const clonedLinks = edit.draft.document.links as { id: string; normalized_url: string }[];
    expect(clonedLinks).toHaveLength(2);
    expect(clonedLinks.map((link) => link.id)).not.toContain(linkId);
    expect(clonedLinks.map((link) => link.normalized_url)).toContain("https://cash.app/$example");

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

  it("never mutates a document after it has left draft state", async () => {
    const owner = "500000000000000019";
    const started = await startDraft({
      owner_user_id: owner,
      origin_guild_id: TEST_HOME_GUILD_ID,
      target_scope: "global",
    });
    const afterOrientation = await putStep(started.draft.id, "orientation", {
      owner_user_id: owner,
      expected_revision: 0,
      orientation: "domme",
    });
    expect(afterOrientation.status).toBe(200);

    await env.DB.prepare(
      `UPDATE profile_documents
          SET state = 'published'
        WHERE id = (SELECT document_id FROM profile_drafts WHERE id = ?)`,
    )
      .bind(started.draft.id)
      .run();

    const result = await putStep(started.draft.id, "identity", {
      owner_user_id: owner,
      expected_revision: 1,
      pronouns: ["She/Her"],
      dm_status: "open",
      dm_status_selected: true,
      bio: "must not be written",
    });
    expect(result.status).toBe(409);
    expect(result.error?.code).toBe("stale_revision");

    const row = await env.DB.prepare(
      `SELECT d.revision, p.state, p.bio
         FROM profile_drafts d
         JOIN profile_documents p ON p.id = d.document_id
        WHERE d.id = ?`,
    )
      .bind(started.draft.id)
      .first<{ revision: number; state: string; bio: string | null }>();
    expect(row).toEqual({ revision: 1, state: "published", bio: null });
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

    const selected = await putStep(draftId, "identity", {
      owner_user_id: owner,
      expected_revision: 1,
      complete: false,
      dm_status: "closed",
      dm_status_selected: true,
    });
    expect(selected.draft?.dm_status_selected).toBe(true);

    const restarted = await restart(draftId, { owner_user_id: owner, expected_revision: 2 });
    expect(restarted.status).toBe(200);
    expect(restarted.draft?.revision).toBe(3);
    expect(restarted.draft?.current_step).toBe("orientation");
    expect(restarted.draft?.steps.every((s) => s.status === "pending")).toBe(true);
    expect(restarted.draft?.dm_status_selected).toBe(false);
    expect((restarted.draft?.document as { orientation?: unknown } | undefined)?.orientation).toBeUndefined();

    const reread = await getDraft(draftId, owner);
    expect(reread.draft?.dm_status_selected).toBe(false);
    expect(reread.draft?.document.selections).toEqual({ pronouns: [], honourifics: [], submissive_labels: [] });
  });

  it.each([
    {
      label: "global",
      owner: "500000000000000091",
      draftId: "legacy-global-draft",
      documentId: "legacy-global-document",
      targetScope: "global",
      guildId: null,
      serverMode: null,
      originGuildId: TEST_HOME_GUILD_ID,
    },
    {
      label: "independent",
      owner: "500000000000000092",
      draftId: "legacy-independent-draft",
      documentId: "legacy-independent-document",
      targetScope: "server",
      guildId: OTHER_GUILD,
      serverMode: "independent",
      originGuildId: OTHER_GUILD,
    },
  ])(
    "requires a deliberate status for a legacy $label draft whose Open value was implicit",
    async ({
      owner,
      draftId,
      documentId,
      targetScope,
      guildId,
      serverMode,
      originGuildId,
    }) => {
      const now = new Date().toISOString();
      await env.DB.batch([
        env.DB.prepare(
          `INSERT INTO profile_documents
             (id, owner_user_id, state, orientation, dm_status, created_at, updated_at)
           VALUES (?, ?, 'draft', 'domme', 'open', ?, ?)`,
        ).bind(documentId, owner, now, now),
        env.DB.prepare(
          `INSERT INTO profile_drafts
             (id, owner_user_id, origin_guild_id, target_scope, guild_id, server_mode,
              document_id, base_version, status, current_step, revision, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, 0, 'active', 'review', 4, ?, ?)`,
        ).bind(
          draftId,
          owner,
          originGuildId,
          targetScope,
          guildId,
          serverMode,
          documentId,
          now,
          now,
        ),
        ...["orientation", "identity", "links", "throne"].map((step) =>
          env.DB.prepare(
            `INSERT INTO profile_draft_steps (draft_id, step_key, status, completed_at)
             VALUES (?, ?, 'completed', ?)`,
          ).bind(draftId, step, now),
        ),
      ]);

      const legacy = await getDraft(draftId, owner);
      expect(legacy.draft?.document.dm_status).toBe("open");
      expect(legacy.draft?.dm_status_selected).toBe(false);
      expect(legacy.draft?.next_step).toBe("identity");
      expect(legacy.draft?.steps.find((step) => step.key === "identity")?.status).toBe("pending");

      const implicitCompletion = await putStep(draftId, "identity", {
        owner_user_id: owner,
        expected_revision: 4,
        dm_status: "open",
      });
      expect(implicitCompletion.status).toBe(400);
      expect(implicitCompletion.error?.code).toBe("dm_status_selection_required");

      const implicitPublish = await publish(draftId, {
        owner_user_id: owner,
        expected_revision: 4,
      });
      expect(implicitPublish.status).toBe(400);
      expect(implicitPublish.error?.code).toBe("dm_status_selection_required");

      const unrelatedPartial = await putStep(draftId, "identity", {
        owner_user_id: owner,
        expected_revision: 4,
        complete: false,
        pronouns: ["She/Her"],
        dm_status: "open",
      });
      expect(unrelatedPartial.status).toBe(200);
      expect(unrelatedPartial.draft?.dm_status_selected).toBe(false);
      expect(unrelatedPartial.draft?.next_step).toBe("identity");

      const deliberate = await putStep(draftId, "identity", {
        owner_user_id: owner,
        expected_revision: 5,
        complete: false,
        pronouns: ["She/Her"],
        dm_status: "open",
        dm_status_selected: true,
      });
      expect(deliberate.status).toBe(200);
      expect(deliberate.draft?.dm_status_selected).toBe(true);
      expect(deliberate.draft?.document.dm_status).toBe("open");
    },
  );

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
        pronouns: ["She/Her"],
        dm_status: "open",
        dm_status_selected: true,
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

  it("does not let a stale draft overwrite a root published after the draft started", async () => {
    const owner = "500000000000000018";
    const started = await startDraft({
      owner_user_id: owner,
      origin_guild_id: TEST_HOME_GUILD_ID,
      target_scope: "global",
    });
    const draftId = started.draft.id;
    await putStep(draftId, "orientation", {
      owner_user_id: owner,
      expected_revision: 0,
      orientation: "domme",
    });
    await putStep(draftId, "identity", {
      owner_user_id: owner,
      expected_revision: 1,
      pronouns: ["She/Her"],
      dm_status: "open",
      dm_status_selected: true,
    });
    await putStep(draftId, "links", {
      owner_user_id: owner,
      expected_revision: 2,
      links: [],
    });
    await putStep(draftId, "throne", {
      owner_user_id: owner,
      expected_revision: 3,
      throne_creator_id: null,
      preferred_payment_link_id: null,
    });
    await putStep(draftId, "review", {
      owner_user_id: owner,
      expected_revision: 4,
    });

    const newerDocumentId = "newer-published-document";
    const now = new Date().toISOString();
    await env.DB.batch([
      env.DB
        .prepare(
          `INSERT INTO profile_documents
             (id, owner_user_id, state, orientation, dm_status, created_at, updated_at)
           VALUES (?, ?, 'published', 'domme', 'closed', ?, ?)`,
        )
        .bind(newerDocumentId, owner, now, now),
      env.DB
        .prepare(
          `INSERT INTO global_profiles
             (owner_user_id, current_document_id, version, published_at, created_at, updated_at)
           VALUES (?, ?, 1, ?, ?, ?)`,
        )
        .bind(owner, newerDocumentId, now, now, now),
    ]);

    const result = await publish(draftId, {
      owner_user_id: owner,
      expected_revision: 5,
    });
    expect(result.status).toBe(409);
    expect(result.error?.code).toBe("publish_conflict");

    const root = await env.DB.prepare(
      "SELECT current_document_id, version FROM global_profiles WHERE owner_user_id = ?",
    )
      .bind(owner)
      .first<{ current_document_id: string; version: number }>();
    expect(root).toEqual({ current_document_id: newerDocumentId, version: 1 });

    const staleDocument = await env.DB.prepare(
      `SELECT state
         FROM profile_documents
        WHERE id = (SELECT document_id FROM profile_drafts WHERE id = ?)`,
    )
      .bind(draftId)
      .first<{ state: string }>();
    expect(staleDocument?.state).toBe("draft");
  });
});

describe("profile draft lifecycle (server scope)", () => {
  it("requires nonempty effective inherited pronouns at completion and publish", async () => {
    const owner = "500000000000000194";
    const globalDocumentId = "linked-pronoun-global";
    await seedDocument({
      id: globalDocumentId,
      ownerUserId: owner,
      orientation: "domme",
      dmStatus: "open",
    });
    await seedGlobalProfile(owner, globalDocumentId);
    const started = await startDraft({
      owner_user_id: owner,
      origin_guild_id: OTHER_GUILD,
      target_scope: "server",
      guild_id: OTHER_GUILD,
      server_mode: "linked",
    });

    const missingInherited = await putStep(started.draft.id, "identity", {
      owner_user_id: owner,
      expected_revision: 0,
      overrides: [],
      dm_status_selected: true,
    });
    expect(missingInherited.status).toBe(400);
    expect(missingInherited.error?.code).toBe("pronouns_required");

    await seedSelection(globalDocumentId, "pronoun", "She/Her");
    const identity = await putStep(started.draft.id, "identity", {
      owner_user_id: owner,
      expected_revision: 0,
      overrides: [],
      dm_status_selected: true,
    });
    const links = await putStep(started.draft.id, "links", {
      owner_user_id: owner,
      expected_revision: (identity.draft as DraftBody).revision,
      local_links: [],
      hidden_inherited_link_ids: [],
      preferred_payment_link_id: null,
    });
    const review = await putStep(started.draft.id, "review", {
      owner_user_id: owner,
      expected_revision: (links.draft as DraftBody).revision,
    });
    await env.DB.prepare(
      "DELETE FROM profile_document_selections WHERE document_id = ? AND category = 'pronoun'",
    )
      .bind(globalDocumentId)
      .run();

    const result = await publish(started.draft.id, {
      owner_user_id: owner,
      expected_revision: (review.draft as DraftBody).revision,
    });
    expect(result.status).toBe(400);
    expect(result.error?.code).toBe("pronouns_required");
  });

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
      dm_status_selected: true,
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
      dm_status_selected: true,
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

    const emptyPronounOverride = await putStep(linkedDraftId, "identity", {
      owner_user_id: owner,
      expected_revision: 0,
      overrides: ["pronouns"],
      pronouns: [],
      dm_status_selected: true,
    });
    expect(emptyPronounOverride.status).toBe(400);
    expect(emptyPronounOverride.error?.code).toBe("pronouns_required");

    const afterIdentity = await putStep(linkedDraftId, "identity", {
      owner_user_id: owner,
      expected_revision: 0,
      overrides: ["dm_status"],
      dm_status: "closed",
      dm_status_selected: true,
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
    const restoredInheritance = await putStep(linkedDraftId, "identity", {
      owner_user_id: owner,
      expected_revision: 3,
      complete: false,
      overrides: [],
      dm_status: null,
      dm_status_selected: true,
    });
    expect(restoredInheritance.status).toBe(200);
    expect(restoredInheritance.draft?.next_step).toBeNull();
    expect(restoredInheritance.draft?.dm_status_selected).toBe(true);
    expect(restoredInheritance.draft?.document.dm_status).toBeNull();
    expect(restoredInheritance.draft?.document.overridden_fields).not.toContain("dm_status");

    const published = await publish(linkedDraftId, { owner_user_id: owner, expected_revision: 4 });
    expect(published.status).toBe(200);
    expect(published.profile?.mode).toBe("linked");
    expect(published.profile?.dm_status).toBe("open");
    expect(published.profile?.bio).toBe("global bio");

    const looked = await lookup(OTHER_GUILD_2, owner);
    expect(looked.profile?.dm_status).toBe("open");
    expect(looked.profile?.bio).toBe("global bio");
    const links = looked.profile?.links as { platform: string }[];
    expect(links.some((l) => l.platform === "onlyfans")).toBe(true);
    expect(links.some((l) => l.platform === "cashapp")).toBe(true);
  });
});
