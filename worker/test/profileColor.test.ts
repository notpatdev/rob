import { describe, expect, it } from "vitest";
import { env } from "cloudflare:test";
import {
  LIMITS,
  PROFILE_COLOR_PRESETS,
  parseIdentityStep,
  parseLinkedIdentityStep,
  parseOptionalColor,
  ValidationError,
} from "../src/profile/contracts";
import { readDocumentSnapshot } from "../src/profile/documentStore";
import { resolveProfile } from "../src/profile/resolver";
import { authHeaders, callWorker, jsonRequest, readJson, TEST_HOME_GUILD_ID } from "./helpers";
import { seedDocument, seedGlobalProfile, seedOverride, seedSelection, seedServerProfile } from "./profileHelpers";

const OTHER_GUILD = "710000000000000001";
const ROSE = 0xe0568a;
const TEAL = 0x2aa198;

interface DraftBody {
  id: string;
  revision: number;
  resolved_profile_color: number | null;
  document: { profile_color: number | null; overridden_fields: string[] };
}
interface DraftEnvelope {
  data: { draft: DraftBody };
  error?: { code: string; message: string };
}

async function startDraft(body: Record<string, unknown>): Promise<DraftBody> {
  const response = await callWorker(jsonRequest("POST", "/v1/profile-drafts/start", body, authHeaders()));
  const parsed = await readJson<DraftEnvelope>(response);
  return parsed.data.draft;
}

async function putStep(draftId: string, stepKey: string, body: Record<string, unknown>) {
  const response = await callWorker(
    jsonRequest("PUT", `/v1/profile-drafts/${draftId}/steps/${stepKey}`, body, authHeaders()),
  );
  const parsed = await readJson<DraftEnvelope>(response);
  return { status: response.status, draft: parsed.data?.draft, error: parsed.error };
}

async function getDraft(draftId: string, owner: string): Promise<DraftBody> {
  const response = await callWorker(
    jsonRequest("GET", `/v1/profile-drafts/${draftId}?owner_user_id=${owner}`, undefined, authHeaders()),
  );
  return (await readJson<DraftEnvelope>(response)).data.draft;
}

async function publish(draftId: string, owner: string, expectedRevision: number) {
  const response = await callWorker(
    jsonRequest(
      "POST",
      `/v1/profile-drafts/${draftId}/publish`,
      { owner_user_id: owner, expected_revision: expectedRevision },
      authHeaders(),
    ),
  );
  const parsed = await readJson<{ data?: { profile: Record<string, unknown> }; error?: { code: string } }>(response);
  return { status: response.status, profile: parsed.data?.profile, error: parsed.error };
}

async function lookup(guildId: string, userId: string) {
  const response = await callWorker(
    jsonRequest("GET", `/v1/guilds/${guildId}/profiles/${userId}`, undefined, authHeaders()),
  );
  return (await readJson<{ data: { profile: Record<string, unknown> | null } }>(response)).data.profile;
}

function identityBody(owner: string, revision: number, extra: Record<string, unknown>): Record<string, unknown> {
  return {
    owner_user_id: owner,
    expected_revision: revision,
    pronouns: ["She/Her"],
    honourifics: [],
    submissive_labels: [],
    dm_status: "open",
    dm_status_selected: true,
    bio: null,
    public_send_stats: false,
    aliases: [],
    ...extra,
  };
}

describe("profile_color parsing", () => {
  it("accepts any in-range sRGB integer, and treats absent/null as no colour", () => {
    expect(parseOptionalColor(0)).toBe(0);
    expect(parseOptionalColor(ROSE)).toBe(ROSE);
    expect(parseOptionalColor(LIMITS.profileColorMax)).toBe(LIMITS.profileColorMax);
    expect(parseOptionalColor(null)).toBeNull();
    expect(parseOptionalColor(undefined)).toBeNull();
  });

  it("keeps every documented preset inside the storage range", () => {
    expect(PROFILE_COLOR_PRESETS.length).toBeGreaterThan(0);
    for (const preset of PROFILE_COLOR_PRESETS) {
      expect(parseOptionalColor(preset.value)).toBe(preset.value);
    }
    expect(new Set(PROFILE_COLOR_PRESETS.map((preset) => preset.value)).size).toBe(PROFILE_COLOR_PRESETS.length);
  });

  it("rejects out-of-range and non-integer colours", () => {
    expect(() => parseOptionalColor(-1)).toThrow(ValidationError);
    expect(() => parseOptionalColor(LIMITS.profileColorMax + 1)).toThrow(ValidationError);
    try {
      parseOptionalColor(0x1000000);
    } catch (error) {
      expect((error as ValidationError).code).toBe("invalid_profile_color");
    }
    for (const bad of [1.5, "#e0568a", "e0568a", true, {}, []]) {
      expect(() => parseOptionalColor(bad)).toThrow(ValidationError);
    }
    try {
      parseOptionalColor("#e0568a");
    } catch (error) {
      expect((error as ValidationError).code).toBe("invalid_field");
    }
  });

  it("parses the colour on the identity step for every orientation, including partial bodies", () => {
    const base = {
      pronouns: ["She/Her"],
      honourifics: [],
      submissive_labels: [],
      dm_status: null,
      bio: null,
      public_send_stats: false,
      aliases: [],
    };
    expect(parseIdentityStep({ ...base, profile_color: ROSE }, "domme", true).profileColor).toBe(ROSE);
    expect(parseIdentityStep({ ...base, profile_color: null }, "domme", true).profileColor).toBeNull();
    expect(parseIdentityStep(base, "domme", true).profileColor).toBeNull();
    expect(
      parseIdentityStep({ ...base, submissive_labels: ["Brat"], profile_color: TEAL }, "submissive", true).profileColor,
    ).toBe(TEAL);
    expect(() => parseIdentityStep({ ...base, profile_color: -5 }, "domme", true)).toThrow(ValidationError);
  });

  it("only reads a linked overlay's colour when profile_color is an explicit override", () => {
    const base = {
      pronouns: [],
      honourifics: [],
      submissive_labels: [],
      dm_status: null,
      bio: null,
      public_send_stats: false,
      aliases: [],
    };
    const inherited = parseLinkedIdentityStep({ ...base, overrides: [], profile_color: ROSE }, "domme");
    expect(inherited.overriddenFields.has("profile_color")).toBe(false);
    expect(inherited.profileColor).toBeNull();

    const overriddenValue = parseLinkedIdentityStep(
      { ...base, overrides: ["profile_color"], profile_color: ROSE },
      "domme",
    );
    expect(overriddenValue.overriddenFields.has("profile_color")).toBe(true);
    expect(overriddenValue.profileColor).toBe(ROSE);

    const overriddenNull = parseLinkedIdentityStep(
      { ...base, overrides: ["profile_color"], profile_color: null },
      "domme",
    );
    expect(overriddenNull.overriddenFields.has("profile_color")).toBe(true);
    expect(overriddenNull.profileColor).toBeNull();

    expect(() =>
      parseLinkedIdentityStep({ ...base, overrides: ["profile_color"], profile_color: 0x1000000 }, "domme"),
    ).toThrow(ValidationError);
  });
});

describe("profile_color document persistence", () => {
  it("round-trips the colour through the identity step, and clears it back to no colour", async () => {
    const owner = "710000000000000010";
    const draft = await startDraft({
      owner_user_id: owner,
      origin_guild_id: TEST_HOME_GUILD_ID,
      target_scope: "global",
    });
    await putStep(draft.id, "orientation", { owner_user_id: owner, expected_revision: 0, orientation: "domme" });

    const coloured = await putStep(draft.id, "identity", identityBody(owner, 1, { profile_color: ROSE, complete: false }));
    expect(coloured.status).toBe(200);
    expect(coloured.draft?.document.profile_color).toBe(ROSE);
    expect((await getDraft(draft.id, owner)).document.profile_color).toBe(ROSE);

    const stored = await env.DB.prepare(
      "SELECT profile_color FROM profile_documents WHERE id = (SELECT document_id FROM profile_drafts WHERE id = ?)",
    )
      .bind(draft.id)
      .first<{ profile_color: number | null }>();
    expect(stored?.profile_color).toBe(ROSE);

    const cleared = await putStep(draft.id, "identity", identityBody(owner, 2, { profile_color: null, complete: false }));
    expect(cleared.draft?.document.profile_color).toBeNull();
    expect((await getDraft(draft.id, owner)).document.profile_color).toBeNull();
  });

  it("rejects an out-of-range colour without touching the stored document", async () => {
    const owner = "710000000000000011";
    const draft = await startDraft({
      owner_user_id: owner,
      origin_guild_id: TEST_HOME_GUILD_ID,
      target_scope: "global",
    });
    await putStep(draft.id, "orientation", { owner_user_id: owner, expected_revision: 0, orientation: "domme" });
    await putStep(draft.id, "identity", identityBody(owner, 1, { profile_color: TEAL, complete: false }));

    const rejected = await putStep(draft.id, "identity", identityBody(owner, 2, { profile_color: 0xffffff + 1 }));
    expect(rejected.status).toBe(400);
    expect(rejected.error?.code).toBe("invalid_profile_color");

    const after = await getDraft(draft.id, owner);
    expect(after.document.profile_color).toBe(TEAL);
    expect(after.revision).toBe(2);
  });

  it("keeps the colour when other services rewrite the document snapshot", async () => {
    const owner = "710000000000000012";
    const draft = await startDraft({
      owner_user_id: owner,
      origin_guild_id: TEST_HOME_GUILD_ID,
      target_scope: "global",
    });
    await putStep(draft.id, "orientation", { owner_user_id: owner, expected_revision: 0, orientation: "domme" });
    await putStep(draft.id, "identity", identityBody(owner, 1, { profile_color: ROSE }));

    const added = await callWorker(
      jsonRequest(
        "POST",
        `/v1/profile-drafts/${draft.id}/links`,
        {
          owner_user_id: owner,
          expected_revision: 2,
          platform: "bluesky",
          public_label: "Bluesky",
          normalized_url: "https://bsky.app/profile/example.test",
          link_type: "social",
        },
        authHeaders(),
      ),
    );
    expect(added.status).toBe(201);
    expect((await getDraft(draft.id, owner)).document.profile_color).toBe(ROSE);
  });

  it("keeps the colour when the orientation changes and its capabilities are renormalized", async () => {
    const owner = "710000000000000014";
    const draft = await startDraft({
      owner_user_id: owner,
      origin_guild_id: TEST_HOME_GUILD_ID,
      target_scope: "global",
    });
    await putStep(draft.id, "orientation", { owner_user_id: owner, expected_revision: 0, orientation: "domme" });
    await putStep(draft.id, "identity", identityBody(owner, 1, { profile_color: ROSE, complete: false }));

    const reoriented = await putStep(draft.id, "orientation", {
      owner_user_id: owner,
      expected_revision: 2,
      orientation: "submissive",
    });
    expect(reoriented.status).toBe(200);
    expect(reoriented.draft?.document.profile_color).toBe(ROSE);
  });

  it("clones the published colour into the next draft and clears it on restart", async () => {
    const owner = "710000000000000013";
    const first = await startDraft({
      owner_user_id: owner,
      origin_guild_id: TEST_HOME_GUILD_ID,
      target_scope: "global",
    });
    await putStep(first.id, "orientation", { owner_user_id: owner, expected_revision: 0, orientation: "domme" });
    await putStep(first.id, "identity", identityBody(owner, 1, { profile_color: ROSE }));
    await putStep(first.id, "links", { owner_user_id: owner, expected_revision: 2, links: [] });
    await putStep(first.id, "throne", { owner_user_id: owner, expected_revision: 3, throne_creator_id: null });
    await putStep(first.id, "review", { owner_user_id: owner, expected_revision: 4 });
    const published = await publish(first.id, owner, 5);
    expect(published.status).toBe(200);
    expect(published.profile?.profile_color).toBe(ROSE);

    // A brand new draft clones the currently published document, colour included.
    const second = await startDraft({
      owner_user_id: owner,
      origin_guild_id: TEST_HOME_GUILD_ID,
      target_scope: "global",
    });
    expect(second.document.profile_color).toBe(ROSE);
    expect(second.id).not.toBe(first.id);

    // Restart re-clones from the same published root, so the colour survives that too.
    const restarted = await callWorker(
      jsonRequest(
        "POST",
        `/v1/profile-drafts/${second.id}/restart`,
        { owner_user_id: owner, expected_revision: second.revision },
        authHeaders(),
      ),
    );
    const restartedDraft = (await readJson<DraftEnvelope>(restarted)).data.draft;
    expect(restartedDraft.document.profile_color).toBe(ROSE);

    const snapshot = await readDocumentSnapshot(
      env,
      (
        await env.DB.prepare("SELECT document_id FROM profile_drafts WHERE id = ?")
          .bind(second.id)
          .first<{ document_id: string }>()
      )?.document_id as string,
    );
    expect(snapshot?.profileColor).toBe(ROSE);
  });
});

describe("profile_color resolution", () => {
  it("publishes the colour on a global profile and exposes it to viewers", async () => {
    await seedDocument({ id: "colour-doc-global", ownerUserId: "720", orientation: "domme", dmStatus: "open", profileColor: ROSE });
    await seedGlobalProfile("720", "colour-doc-global");

    const resolved = await resolveProfile(env, TEST_HOME_GUILD_ID, "720");
    expect(resolved.profile?.profileColor).toBe(ROSE);
    expect(await lookup(TEST_HOME_GUILD_ID, "720")).toMatchObject({ profile_color: ROSE });
  });

  it("resolves an independent server profile's own colour, ignoring the global one", async () => {
    await seedDocument({ id: "colour-doc-g2", ownerUserId: "721", orientation: "domme", dmStatus: "open", profileColor: ROSE });
    await seedGlobalProfile("721", "colour-doc-g2");
    await seedDocument({ id: "colour-doc-i2", ownerUserId: "721", orientation: "domme", dmStatus: "open", profileColor: TEAL });
    await seedServerProfile({
      id: "colour-srv-2",
      guildId: OTHER_GUILD,
      ownerUserId: "721",
      mode: "independent",
      documentId: "colour-doc-i2",
    });

    const resolved = await resolveProfile(env, OTHER_GUILD, "721");
    expect(resolved.profile?.profileColor).toBe(TEAL);
  });

  it("inherits the global colour on a linked overlay with no profile_color override", async () => {
    await seedDocument({ id: "colour-doc-g3", ownerUserId: "722", orientation: "domme", dmStatus: "open", profileColor: ROSE });
    await seedGlobalProfile("722", "colour-doc-g3");
    // The overlay's own column is NULL and must not be mistaken for "no colour".
    await seedDocument({ id: "colour-doc-l3", ownerUserId: "722", orientation: "domme", profileColor: null });
    await seedServerProfile({
      id: "colour-srv-3",
      guildId: OTHER_GUILD,
      ownerUserId: "722",
      mode: "linked",
      documentId: "colour-doc-l3",
    });

    const resolved = await resolveProfile(env, OTHER_GUILD, "722");
    expect(resolved.profile?.profileColor).toBe(ROSE);
  });

  it("uses a linked overlay's own colour when profile_color is overridden", async () => {
    await seedDocument({ id: "colour-doc-g4", ownerUserId: "723", orientation: "domme", dmStatus: "open", profileColor: ROSE });
    await seedGlobalProfile("723", "colour-doc-g4");
    await seedDocument({ id: "colour-doc-l4", ownerUserId: "723", orientation: "domme", profileColor: TEAL });
    await seedOverride("colour-doc-l4", "profile_color");
    await seedServerProfile({
      id: "colour-srv-4",
      guildId: OTHER_GUILD,
      ownerUserId: "723",
      mode: "linked",
      documentId: "colour-doc-l4",
    });

    const resolved = await resolveProfile(env, OTHER_GUILD, "723");
    expect(resolved.profile?.profileColor).toBe(TEAL);
  });

  it("treats an override row with a NULL colour as a deliberate 'no colour', not inheritance", async () => {
    await seedDocument({ id: "colour-doc-g5", ownerUserId: "724", orientation: "domme", dmStatus: "open", profileColor: ROSE });
    await seedGlobalProfile("724", "colour-doc-g5");
    await seedDocument({ id: "colour-doc-l5", ownerUserId: "724", orientation: "domme", profileColor: null });
    await seedOverride("colour-doc-l5", "profile_color");
    await seedServerProfile({
      id: "colour-srv-5",
      guildId: OTHER_GUILD,
      ownerUserId: "724",
      mode: "linked",
      documentId: "colour-doc-l5",
    });

    const resolved = await resolveProfile(env, OTHER_GUILD, "724");
    expect(resolved.profile?.profileColor).toBeNull();
    expect(await lookup(OTHER_GUILD, "724")).toMatchObject({ profile_color: null });
  });
});

describe("profile_color on a linked server draft", () => {
  it("persists an override, then a deliberate clear, then a return to inheritance", async () => {
    const owner = "710000000000000020";
    await seedDocument({
      id: "colour-linked-global",
      ownerUserId: owner,
      orientation: "domme",
      dmStatus: "open",
      profileColor: ROSE,
    });
    await seedGlobalProfile(owner, "colour-linked-global");

    const draft = await startDraft({
      owner_user_id: owner,
      origin_guild_id: TEST_HOME_GUILD_ID,
      target_scope: "server",
      guild_id: OTHER_GUILD,
      server_mode: "linked",
    });
    expect(draft.resolved_profile_color).toBe(ROSE);

    const overridden = await putStep(draft.id, "identity", {
      owner_user_id: owner,
      expected_revision: draft.revision,
      overrides: ["profile_color"],
      profile_color: TEAL,
      complete: false,
    });
    expect(overridden.status).toBe(200);
    expect(overridden.draft?.document.profile_color).toBe(TEAL);
    expect(overridden.draft?.document.overridden_fields).toContain("profile_color");
    expect(overridden.draft?.resolved_profile_color).toBe(TEAL);

    const explicitNone = await putStep(draft.id, "identity", {
      owner_user_id: owner,
      expected_revision: (overridden.draft as DraftBody).revision,
      overrides: ["profile_color"],
      profile_color: null,
      complete: false,
    });
    expect(explicitNone.draft?.document.profile_color).toBeNull();
    expect(explicitNone.draft?.document.overridden_fields).toContain("profile_color");

    const inherited = await putStep(draft.id, "identity", {
      owner_user_id: owner,
      expected_revision: (explicitNone.draft as DraftBody).revision,
      overrides: [],
      profile_color: TEAL,
      complete: false,
    });
    expect(inherited.draft?.document.overridden_fields).not.toContain("profile_color");
    expect(inherited.draft?.document.profile_color).toBeNull();
    expect(inherited.draft?.resolved_profile_color).toBe(ROSE);
  });

  it("publishes a linked overlay whose overridden colour beats the inherited one", async () => {
    const owner = "710000000000000021";
    await seedDocument({
      id: "colour-linked-global-2",
      ownerUserId: owner,
      orientation: "domme",
      dmStatus: "open",
      profileColor: ROSE,
    });
    await seedSelection("colour-linked-global-2", "pronoun", "She/Her");
    await seedGlobalProfile(owner, "colour-linked-global-2");

    const draft = await startDraft({
      owner_user_id: owner,
      origin_guild_id: TEST_HOME_GUILD_ID,
      target_scope: "server",
      guild_id: OTHER_GUILD,
      server_mode: "linked",
    });
    const identity = await putStep(draft.id, "identity", {
      owner_user_id: owner,
      expected_revision: draft.revision,
      overrides: ["profile_color"],
      profile_color: TEAL,
      dm_status_selected: true,
    });
    const links = await putStep(draft.id, "links", {
      owner_user_id: owner,
      expected_revision: (identity.draft as DraftBody).revision,
      links: [],
      hidden_inherited_link_ids: [],
    });
    const review = await putStep(draft.id, "review", {
      owner_user_id: owner,
      expected_revision: (links.draft as DraftBody).revision,
    });
    const published = await publish(draft.id, owner, (review.draft as DraftBody).revision);
    expect(published.status).toBe(200);
    expect(published.profile?.profile_color).toBe(TEAL);
    expect(await lookup(OTHER_GUILD, owner)).toMatchObject({ profile_color: TEAL, mode: "linked" });
  });
});
