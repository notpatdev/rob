import { describe, expect, it } from "vitest";
import { env } from "cloudflare:test";
import {
  deriveWizardStage,
  setDraftWizardStage,
  type DraftContract,
} from "../src/profile/draftService";
import { WIZARD_STAGES, wizardStagesForDraft } from "../src/profile/contracts";
import { authHeaders, callWorker, jsonRequest, readJson, TEST_HOME_GUILD_ID } from "./helpers";
import { seedDocument, seedGlobalProfile } from "./profileHelpers";

const LINKED_GUILD = "740000000000000001";

interface DraftBody {
  id: string;
  revision: number;
  status: string;
  current_step: string;
  next_step: string | null;
  governing_orientation: string | null;
  wizard_stage: string;
  wizard_substep: string | null;
  document: Record<string, unknown>;
}
interface DraftEnvelope {
  data?: { draft: DraftBody };
  error?: { code: string; message: string };
}

async function startDraft(body: Record<string, unknown>): Promise<DraftBody> {
  const response = await callWorker(jsonRequest("POST", "/v1/profile-drafts/start", body, authHeaders()));
  return (await readJson<DraftEnvelope>(response)).data!.draft;
}

async function putStep(draftId: string, stepKey: string, body: Record<string, unknown>) {
  const response = await callWorker(
    jsonRequest("PUT", `/v1/profile-drafts/${draftId}/steps/${stepKey}`, body, authHeaders()),
  );
  const parsed = await readJson<DraftEnvelope>(response);
  return { status: response.status, draft: parsed.data?.draft, error: parsed.error };
}

async function setStage(draftId: string, body: Record<string, unknown>, headers = authHeaders()) {
  const response = await callWorker(
    jsonRequest("PUT", `/v1/profile-drafts/${draftId}/wizard-stage`, body, headers),
  );
  const parsed = await readJson<DraftEnvelope>(response);
  return { status: response.status, draft: parsed.data?.draft, error: parsed.error };
}

async function getDraft(draftId: string, owner: string): Promise<DraftBody> {
  const response = await callWorker(
    jsonRequest("GET", `/v1/profile-drafts/${draftId}?owner_user_id=${owner}`, undefined, authHeaders()),
  );
  return (await readJson<DraftEnvelope>(response)).data!.draft;
}

async function storedBookmark(draftId: string) {
  return env.DB.prepare("SELECT wizard_stage, wizard_substep, revision FROM profile_drafts WHERE id = ?")
    .bind(draftId)
    .first<{ wizard_stage: string | null; wizard_substep: string | null; revision: number }>();
}

function identityBody(owner: string, revision: number, extra: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    owner_user_id: owner,
    expected_revision: revision,
    pronouns: ["She/Her"],
    honourifics: [],
    submissive_labels: [],
    dm_status: "open",
    bio: null,
    public_send_stats: false,
    aliases: [],
    ...extra,
  };
}

/** Starts a global draft and completes its orientation step. */
async function startOrientedDraft(owner: string, orientation: string): Promise<DraftBody> {
  const draft = await startDraft({
    owner_user_id: owner,
    origin_guild_id: TEST_HOME_GUILD_ID,
    target_scope: "global",
  });
  const oriented = await putStep(draft.id, "orientation", {
    owner_user_id: owner,
    expected_revision: draft.revision,
    orientation,
  });
  return oriented.draft as DraftBody;
}

async function startLinkedDraft(owner: string): Promise<DraftBody> {
  await seedDocument({ id: `stage-global-${owner}`, ownerUserId: owner, orientation: "domme", dmStatus: "open" });
  await seedGlobalProfile(owner, `stage-global-${owner}`);
  return startDraft({
    owner_user_id: owner,
    origin_guild_id: TEST_HOME_GUILD_ID,
    target_scope: "server",
    guild_id: LINKED_GUILD,
    server_mode: "linked",
  });
}

describe("wizard stage sequences", () => {
  it("matches the bot's per-orientation stage sequence", () => {
    expect(wizardStagesForDraft("global", null, "domme")).toEqual([
      "orientation",
      "pronouns",
      "honourifics",
      "dm_status",
      "bio",
      "profile_color",
      "links",
      "throne",
      "review",
    ]);
    expect(wizardStagesForDraft("global", null, "submissive")).toEqual([
      "orientation",
      "pronouns",
      "submissive_labels",
      "dm_status",
      "bio",
      "profile_color",
      "links",
      "details",
      "review",
    ]);
    expect(wizardStagesForDraft("global", null, "switch_domme")).toEqual([
      "orientation",
      "pronouns",
      "honourifics",
      "submissive_labels",
      "dm_status",
      "bio",
      "profile_color",
      "links",
      "throne",
      "details",
      "review",
    ]);
  });

  it("drops orientation and throne for a linked overlay, and offers everything before orientation is chosen", () => {
    expect(wizardStagesForDraft("server", "linked", "domme")).toEqual([
      "pronouns",
      "honourifics",
      "dm_status",
      "bio",
      "profile_color",
      "links",
      "review",
    ]);
    expect(wizardStagesForDraft("server", "independent", "domme")).toContain("throne");
    expect(wizardStagesForDraft("global", null, null)).toEqual([
      "orientation",
      "pronouns",
      "honourifics",
      "submissive_labels",
      "dm_status",
      "bio",
      "profile_color",
      "links",
      "details",
      "review",
    ]);
    for (const stage of WIZARD_STAGES) {
      expect(WIZARD_STAGES.indexOf(stage)).toBeGreaterThanOrEqual(0);
    }
  });

  it("derives a stage from a draft's coarse progress, clamping to the applicable sequence", () => {
    const domme = wizardStagesForDraft("global", null, "domme");
    expect(deriveWizardStage(domme, "orientation", "orientation")).toBe("orientation");
    expect(deriveWizardStage(domme, "identity", "orientation")).toBe("pronouns");
    expect(deriveWizardStage(domme, "links", "identity")).toBe("links");
    expect(deriveWizardStage(domme, "throne", "links")).toBe("throne");
    expect(deriveWizardStage(domme, "review", "throne")).toBe("review");
    expect(deriveWizardStage(domme, null, "review")).toBe("review");

    const linked = wizardStagesForDraft("server", "linked", "domme");
    // `orientation` is not a linked draft's screen; the nearest applicable one is used instead.
    expect(deriveWizardStage(linked, "orientation", "orientation")).toBe("pronouns");
    const submissive = wizardStagesForDraft("global", null, "submissive");
    expect(deriveWizardStage(submissive, "throne", "links")).toBe("links");
  });
});

describe("PUT /v1/profile-drafts/:draftId/wizard-stage", () => {
  it("persists the stage and substep, bumping the revision like any other mutation", async () => {
    const owner = "740000000000000010";
    const draft = await startOrientedDraft(owner, "domme");

    const moved = await setStage(draft.id, {
      owner_user_id: owner,
      expected_revision: draft.revision,
      stage: "throne",
      substep: "awaiting_verification",
    });
    expect(moved.status).toBe(200);
    expect(moved.draft?.wizard_stage).toBe("throne");
    expect(moved.draft?.wizard_substep).toBe("awaiting_verification");
    expect(moved.draft?.revision).toBe(draft.revision + 1);

    expect(await storedBookmark(draft.id)).toMatchObject({
      wizard_stage: "throne",
      wizard_substep: "awaiting_verification",
      revision: draft.revision + 1,
    });

    const reloaded = await getDraft(draft.id, owner);
    expect(reloaded.wizard_stage).toBe("throne");
    expect(reloaded.wizard_substep).toBe("awaiting_verification");
  });

  it("clears a previous substep when the next navigation omits one", async () => {
    const owner = "740000000000000011";
    const draft = await startOrientedDraft(owner, "domme");
    const verified = await setStage(draft.id, {
      owner_user_id: owner,
      expected_revision: draft.revision,
      stage: "throne",
      substep: "verified",
    });
    expect(verified.draft?.wizard_substep).toBe("verified");

    const moved = await setStage(draft.id, {
      owner_user_id: owner,
      expected_revision: (verified.draft as DraftBody).revision,
      stage: "review",
    });
    expect(moved.status).toBe(200);
    expect(moved.draft?.wizard_stage).toBe("review");
    expect(moved.draft?.wizard_substep).toBeNull();
    expect(await storedBookmark(draft.id)).toMatchObject({ wizard_substep: null });

    const explicitNull = await setStage(draft.id, {
      owner_user_id: owner,
      expected_revision: (moved.draft as DraftBody).revision,
      stage: "bio",
      substep: null,
    });
    expect(explicitNull.draft?.wizard_substep).toBeNull();
  });

  it("rejects a stale expected_revision without moving the bookmark", async () => {
    const owner = "740000000000000012";
    const draft = await startOrientedDraft(owner, "domme");
    const first = await setStage(draft.id, {
      owner_user_id: owner,
      expected_revision: draft.revision,
      stage: "bio",
      substep: "review",
    });
    expect(first.status).toBe(200);

    const stale = await setStage(draft.id, {
      owner_user_id: owner,
      expected_revision: draft.revision,
      stage: "links",
    });
    expect(stale.status).toBe(409);
    expect(stale.error?.code).toBe("stale_revision");
    expect(await storedBookmark(draft.id)).toMatchObject({
      wizard_stage: "bio",
      wizard_substep: "review",
      revision: draft.revision + 1,
    });
  });

  it("lets exactly one of two racing navigations win", async () => {
    const owner = "740000000000000013";
    const draft = await startOrientedDraft(owner, "domme");

    const attempts = await Promise.allSettled([
      setDraftWizardStage(env, {
        draftId: draft.id,
        ownerUserId: owner,
        expectedRevision: draft.revision,
        stage: "bio",
        substep: null,
      }),
      setDraftWizardStage(env, {
        draftId: draft.id,
        ownerUserId: owner,
        expectedRevision: draft.revision,
        stage: "links",
        substep: null,
      }),
    ]);
    const fulfilled = attempts.filter(
      (attempt): attempt is PromiseFulfilledResult<DraftContract> => attempt.status === "fulfilled",
    );
    const rejected = attempts.filter((attempt): attempt is PromiseRejectedResult => attempt.status === "rejected");
    expect(fulfilled).toHaveLength(1);
    expect(rejected).toHaveLength(1);
    expect(rejected[0]?.reason).toEqual(expect.objectContaining({ code: "stale_revision" }));

    const stored = await storedBookmark(draft.id);
    expect(stored?.revision).toBe(draft.revision + 1);
    expect(stored?.wizard_stage).toBe(fulfilled[0]?.value.wizardStage);
  });

  it("rejects unknown stages, out-of-sequence stages, and malformed substeps", async () => {
    const owner = "740000000000000014";
    const draft = await startOrientedDraft(owner, "submissive");

    const unknown = await setStage(draft.id, {
      owner_user_id: owner,
      expected_revision: draft.revision,
      stage: "colour_wheel",
    });
    expect(unknown.status).toBe(400);
    expect(unknown.error?.code).toBe("invalid_wizard_stage");

    const missing = await setStage(draft.id, { owner_user_id: owner, expected_revision: draft.revision });
    expect(missing.status).toBe(400);
    expect(missing.error?.code).toBe("invalid_wizard_stage");

    // A submissive profile has no Throne screen at all.
    const notApplicable = await setStage(draft.id, {
      owner_user_id: owner,
      expected_revision: draft.revision,
      stage: "throne",
    });
    expect(notApplicable.status).toBe(400);
    expect(notApplicable.error?.code).toBe("stage_not_applicable");

    const badSubstep = await setStage(draft.id, {
      owner_user_id: owner,
      expected_revision: draft.revision,
      stage: "bio",
      substep: "x".repeat(41),
    });
    expect(badSubstep.status).toBe(400);
    expect(badSubstep.error?.code).toBe("invalid_wizard_substep");

    const emptySubstep = await setStage(draft.id, {
      owner_user_id: owner,
      expected_revision: draft.revision,
      stage: "bio",
      substep: "   ",
    });
    expect(emptySubstep.status).toBe(400);
    expect(emptySubstep.error?.code).toBe("invalid_wizard_substep");

    expect(await storedBookmark(draft.id)).toMatchObject({ wizard_stage: null, revision: draft.revision });
  });

  it("refuses a stage that belongs to another draft shape", async () => {
    const owner = "740000000000000015";
    const draft = await startLinkedDraft(owner);
    expect(draft.wizard_stage).toBe("pronouns");

    const orientation = await setStage(draft.id, {
      owner_user_id: owner,
      expected_revision: draft.revision,
      stage: "orientation",
    });
    expect(orientation.status).toBe(400);
    expect(orientation.error?.code).toBe("stage_not_applicable");

    const throne = await setStage(draft.id, {
      owner_user_id: owner,
      expected_revision: draft.revision,
      stage: "throne",
    });
    expect(throne.status).toBe(400);
    expect(throne.error?.code).toBe("stage_not_applicable");

    const allowed = await setStage(draft.id, {
      owner_user_id: owner,
      expected_revision: draft.revision,
      stage: "profile_color",
    });
    expect(allowed.status).toBe(200);
    expect(allowed.draft?.wizard_stage).toBe("profile_color");
  });

  it("requires bearer auth, a valid owner, and an active draft", async () => {
    const owner = "740000000000000016";
    const draft = await startOrientedDraft(owner, "domme");

    const unauthorized = await setStage(
      draft.id,
      { owner_user_id: owner, expected_revision: draft.revision, stage: "bio" },
      {},
    );
    expect(unauthorized.status).toBe(401);

    const otherOwner = await setStage(draft.id, {
      owner_user_id: "740000000000000017",
      expected_revision: draft.revision,
      stage: "bio",
    });
    expect(otherOwner.status).toBe(404);
    expect(otherOwner.error?.code).toBe("draft_not_found");

    const malformedOwner = await setStage(draft.id, {
      owner_user_id: "not-a-snowflake",
      expected_revision: draft.revision,
      stage: "bio",
    });
    expect(malformedOwner.status).toBe(400);
    expect(malformedOwner.error?.code).toBe("invalid_owner_user_id");

    // Publish the draft, then confirm a late navigation cannot revive it.
    let revision = draft.revision;
    revision = ((await putStep(draft.id, "identity", identityBody(owner, revision))).draft as DraftBody).revision;
    revision = ((await putStep(draft.id, "links", { owner_user_id: owner, expected_revision: revision, links: [] }))
      .draft as DraftBody).revision;
    revision = ((
      await putStep(draft.id, "throne", {
        owner_user_id: owner,
        expected_revision: revision,
        throne_creator_id: null,
      })
    ).draft as DraftBody).revision;
    revision = ((await putStep(draft.id, "review", { owner_user_id: owner, expected_revision: revision }))
      .draft as DraftBody).revision;
    const publishResponse = await callWorker(
      jsonRequest(
        "POST",
        `/v1/profile-drafts/${draft.id}/publish`,
        { owner_user_id: owner, expected_revision: revision },
        authHeaders(),
      ),
    );
    expect(publishResponse.status).toBe(200);

    const afterPublish = await setStage(draft.id, {
      owner_user_id: owner,
      expected_revision: revision + 1,
      stage: "bio",
    });
    expect(afterPublish.status).toBe(409);
    expect(afterPublish.error?.code).toBe("draft_not_active");
  });
});

describe("wizard stage compatibility with drafts created before migration 0004", () => {
  it("serializes a derived stage for a NULL bookmark, following the draft's own progress", async () => {
    const owner = "740000000000000020";
    const draft = await startOrientedDraft(owner, "domme");
    // A fresh draft has never been navigated: its stored bookmark is NULL.
    expect(await storedBookmark(draft.id)).toMatchObject({ wizard_stage: null, wizard_substep: null });
    expect(draft.wizard_stage).toBe("pronouns");
    expect(draft.wizard_substep).toBeNull();

    const identity = await putStep(draft.id, "identity", identityBody(owner, draft.revision));
    expect(identity.draft?.next_step).toBe("links");
    expect(identity.draft?.wizard_stage).toBe("links");

    const links = await putStep(draft.id, "links", {
      owner_user_id: owner,
      expected_revision: (identity.draft as DraftBody).revision,
      links: [],
    });
    expect(links.draft?.wizard_stage).toBe("throne");

    const throne = await putStep(draft.id, "throne", {
      owner_user_id: owner,
      expected_revision: (links.draft as DraftBody).revision,
      throne_creator_id: null,
    });
    expect(throne.draft?.wizard_stage).toBe("review");
  });

  it("derives a linked overlay's resume stage even though it has no orientation screen", async () => {
    const owner = "740000000000000021";
    const draft = await startLinkedDraft(owner);
    expect(await storedBookmark(draft.id)).toMatchObject({ wizard_stage: null });
    expect(draft.current_step).toBe("orientation");
    expect(draft.wizard_stage).toBe("pronouns");
  });

  it("ignores a stored bookmark the draft's own orientation no longer allows", async () => {
    const owner = "740000000000000022";
    const draft = await startOrientedDraft(owner, "domme");
    const moved = await setStage(draft.id, {
      owner_user_id: owner,
      expected_revision: draft.revision,
      stage: "throne",
      substep: "verified",
    });
    expect(moved.draft?.wizard_stage).toBe("throne");

    // Switching to an orientation without a Throne step invalidates that bookmark, so the
    // contract falls back to deriving from progress (identity is still pending) instead of
    // echoing a screen the wizard can no longer render. The stale row itself is left alone.
    const reoriented = await putStep(draft.id, "orientation", {
      owner_user_id: owner,
      expected_revision: (moved.draft as DraftBody).revision,
      orientation: "submissive",
    });
    expect(reoriented.draft?.next_step).toBe("identity");
    expect(reoriented.draft?.wizard_stage).toBe("pronouns");
    expect(await storedBookmark(draft.id)).toMatchObject({ wizard_stage: "throne" });

    // Once identity and links are done, the same invalid bookmark derives forward, never to a
    // Throne screen this orientation does not have.
    const identity = await putStep(
      draft.id,
      "identity",
      identityBody(owner, (reoriented.draft as DraftBody).revision, { submissive_labels: ["Brat"] }),
    );
    const links = await putStep(draft.id, "links", {
      owner_user_id: owner,
      expected_revision: (identity.draft as DraftBody).revision,
      links: [],
    });
    expect(links.draft?.next_step).toBe("review");
    expect(links.draft?.wizard_stage).toBe("review");
  });

  it("clears the bookmark when a draft is restarted", async () => {
    const owner = "740000000000000023";
    const draft = await startOrientedDraft(owner, "domme");
    const moved = await setStage(draft.id, {
      owner_user_id: owner,
      expected_revision: draft.revision,
      stage: "bio",
      substep: "review",
    });

    const restarted = await callWorker(
      jsonRequest(
        "POST",
        `/v1/profile-drafts/${draft.id}/restart`,
        { owner_user_id: owner, expected_revision: (moved.draft as DraftBody).revision },
        authHeaders(),
      ),
    );
    const restartedDraft = (await readJson<DraftEnvelope>(restarted)).data!.draft;
    expect(restartedDraft.wizard_stage).toBe("orientation");
    expect(restartedDraft.wizard_substep).toBeNull();
    expect(await storedBookmark(draft.id)).toMatchObject({ wizard_stage: null, wizard_substep: null });
  });
});

describe("optional bookmark carried on a step mutation", () => {
  it("persists stage and substep in the same guarded batch as the step itself", async () => {
    const owner = "740000000000000030";
    const draft = await startOrientedDraft(owner, "domme");

    const identity = await putStep(
      draft.id,
      "identity",
      identityBody(owner, draft.revision, { wizard_stage: "links", wizard_substep: "review" }),
    );
    expect(identity.status).toBe(200);
    expect(identity.draft?.wizard_stage).toBe("links");
    expect(identity.draft?.wizard_substep).toBe("review");
    expect(await storedBookmark(draft.id)).toMatchObject({ wizard_stage: "links", wizard_substep: "review" });

    // Omitting the keys entirely leaves the stored bookmark alone.
    const links = await putStep(draft.id, "links", {
      owner_user_id: owner,
      expected_revision: (identity.draft as DraftBody).revision,
      links: [],
    });
    expect(links.status).toBe(200);
    expect(await storedBookmark(draft.id)).toMatchObject({ wizard_stage: "links", wizard_substep: "review" });

    // An explicit null clears just that column.
    const throne = await putStep(draft.id, "throne", {
      owner_user_id: owner,
      expected_revision: (links.draft as DraftBody).revision,
      throne_creator_id: null,
      wizard_substep: null,
    });
    expect(await storedBookmark(draft.id)).toMatchObject({ wizard_stage: "links", wizard_substep: null });
    expect(throne.draft?.wizard_stage).toBe("links");
  });

  it("rejects an invalid bookmark without applying the step", async () => {
    const owner = "740000000000000031";
    const draft = await startOrientedDraft(owner, "domme");

    const bad = await putStep(
      draft.id,
      "identity",
      identityBody(owner, draft.revision, { bio: "kept out", wizard_stage: "nowhere" }),
    );
    expect(bad.status).toBe(400);
    expect(bad.error?.code).toBe("invalid_wizard_stage");

    const notApplicable = await putStep(
      draft.id,
      "orientation",
      {
        owner_user_id: owner,
        expected_revision: draft.revision,
        orientation: "submissive",
        wizard_stage: "throne",
      },
    );
    expect(notApplicable.status).toBe(400);
    expect(notApplicable.error?.code).toBe("stage_not_applicable");

    const unchanged = await getDraft(draft.id, owner);
    expect(unchanged.revision).toBe(draft.revision);
    expect(unchanged.document.bio).toBeNull();
    expect(unchanged.governing_orientation).toBe("domme");
  });

  it("accepts a bookmark that only becomes valid because of the step being applied", async () => {
    const owner = "740000000000000032";
    const draft = await startDraft({
      owner_user_id: owner,
      origin_guild_id: TEST_HOME_GUILD_ID,
      target_scope: "global",
    });

    const oriented = await putStep(draft.id, "orientation", {
      owner_user_id: owner,
      expected_revision: draft.revision,
      orientation: "domme",
      wizard_stage: "pronouns",
    });
    expect(oriented.status).toBe(200);
    expect(oriented.draft?.wizard_stage).toBe("pronouns");
  });
});
