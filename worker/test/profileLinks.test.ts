import { describe, expect, it } from "vitest";
import { authHeaders, callWorker, jsonRequest, readJson, TEST_HOME_GUILD_ID } from "./helpers";

interface DraftEnvelope {
  data: { resume_required?: boolean; draft: DraftBody };
}
interface DraftBody {
  id: string;
  revision: number;
  document: { links: { id: string; platform: string; public_label: string; link_type: string; enabled: boolean }[]; preferred_payment_link_id: string | null };
}
interface ErrorEnvelope {
  error: { code: string; message: string };
}

async function startGlobalDraft(owner: string) {
  const response = await callWorker(
    jsonRequest("POST", "/v1/profile-drafts/start", { owner_user_id: owner, origin_guild_id: TEST_HOME_GUILD_ID, target_scope: "global" }, authHeaders()),
  );
  const parsed = await readJson<DraftEnvelope>(response);
  return parsed.data.draft;
}

async function setOrientation(draftId: string, owner: string, revision: number, orientation: string) {
  const response = await callWorker(
    jsonRequest("PUT", `/v1/profile-drafts/${draftId}/steps/orientation`, { owner_user_id: owner, expected_revision: revision, orientation }, authHeaders()),
  );
  const parsed = await readJson<DraftEnvelope>(response);
  return parsed.data.draft;
}

async function addLink(draftId: string, body: Record<string, unknown>) {
  const response = await callWorker(jsonRequest("POST", `/v1/profile-drafts/${draftId}/links`, body, authHeaders()));
  const parsed = await readJson<DraftEnvelope & ErrorEnvelope>(response);
  return { status: response.status, draft: parsed.data?.draft, error: parsed.error };
}

async function editLink(draftId: string, linkId: string, body: Record<string, unknown>) {
  const response = await callWorker(jsonRequest("PUT", `/v1/profile-drafts/${draftId}/links/${linkId}`, body, authHeaders()));
  const parsed = await readJson<DraftEnvelope & ErrorEnvelope>(response);
  return { status: response.status, draft: parsed.data?.draft, error: parsed.error };
}

async function deleteLink(draftId: string, linkId: string, body: Record<string, unknown>) {
  const response = await callWorker(jsonRequest("DELETE", `/v1/profile-drafts/${draftId}/links/${linkId}`, body, authHeaders()));
  const parsed = await readJson<DraftEnvelope & ErrorEnvelope>(response);
  return { status: response.status, draft: parsed.data?.draft, error: parsed.error };
}

describe("manual draft link CRUD", () => {
  it("adds a link with automatic known-provider classification", async () => {
    const owner = "600000000000000001";
    const started = await startGlobalDraft(owner);
    await setOrientation(started.id, owner, 0, "domme");

    const added = await addLink(started.id, {
      owner_user_id: owner,
      expected_revision: 1,
      public_label: "My Twitter",
      normalized_url: "https://twitter.com/example",
      // link_type deliberately omitted/wrong -- known-provider classification must win.
      link_type: "payment",
    });
    expect(added.status).toBe(201);
    expect(added.draft?.document.links).toHaveLength(1);
    expect(added.draft?.document.links[0]).toMatchObject({ platform: "twitter", link_type: "social", public_label: "My Twitter" });
  });

  it("requires an explicit link_type/platform for an unrecognized provider", async () => {
    const owner = "600000000000000002";
    const started = await startGlobalDraft(owner);
    await setOrientation(started.id, owner, 0, "domme");

    const missingType = await addLink(started.id, {
      owner_user_id: owner,
      expected_revision: 1,
      public_label: "My Site",
      normalized_url: "https://my-own-site.example/",
    });
    expect(missingType.status).toBe(400);
    expect(missingType.error?.code).toBe("platform_required");

    const withType = await addLink(started.id, {
      owner_user_id: owner,
      expected_revision: 1,
      public_label: "My Site",
      normalized_url: "https://my-own-site.example/",
      platform: "personal-site",
      link_type: "social",
    });
    expect(withType.status).toBe(201);
    expect(withType.draft?.document.links[0]).toMatchObject({ platform: "personal-site", link_type: "social" });
  });

  it("rejects non-https URLs and embedded credentials", async () => {
    const owner = "600000000000000003";
    const started = await startGlobalDraft(owner);
    await setOrientation(started.id, owner, 0, "domme");

    const insecure = await addLink(started.id, {
      owner_user_id: owner,
      expected_revision: 1,
      public_label: "Bad",
      normalized_url: "http://twitter.com/example",
    });
    expect(insecure.status).toBe(400);
    expect(insecure.error?.code).toBe("invalid_url_scheme");

    const withCreds = await addLink(started.id, {
      owner_user_id: owner,
      expected_revision: 1,
      public_label: "Bad",
      normalized_url: "https://user:pass@twitter.com/example",
    });
    expect(withCreds.status).toBe(400);
    expect(withCreds.error?.code).toBe("invalid_url_credentials");
  });

  it("rejects payment links for an orientation without payment capability", async () => {
    const owner = "600000000000000004";
    const started = await startGlobalDraft(owner);
    await setOrientation(started.id, owner, 0, "submissive");

    const result = await addLink(started.id, {
      owner_user_id: owner,
      expected_revision: 1,
      public_label: "Cash",
      normalized_url: "https://cash.app/$example",
    });
    expect(result.status).toBe(400);
    expect(result.error?.code).toBe("payment_links_unavailable");
  });

  it("enforces the twelve-link cap", async () => {
    const owner = "600000000000000005";
    const started = await startGlobalDraft(owner);
    await setOrientation(started.id, owner, 0, "domme");

    let revision = 1;
    for (let i = 0; i < 12; i++) {
      const result = await addLink(started.id, {
        owner_user_id: owner,
        expected_revision: revision,
        public_label: `Site ${i}`,
        normalized_url: `https://site${i}.example/`,
        platform: `site${i}`,
        link_type: "social",
      });
      expect(result.status).toBe(201);
      revision = result.draft!.revision;
    }

    const thirteenth = await addLink(started.id, {
      owner_user_id: owner,
      expected_revision: revision,
      public_label: "One too many",
      normalized_url: "https://site13.example/",
      platform: "site13",
      link_type: "social",
    });
    expect(thirteenth.status).toBe(400);
    expect(thirteenth.error?.code).toBe("too_many_links");
  });

  it("rejects a duplicate normalized URL", async () => {
    const owner = "600000000000000006";
    const started = await startGlobalDraft(owner);
    await setOrientation(started.id, owner, 0, "domme");

    const first = await addLink(started.id, {
      owner_user_id: owner,
      expected_revision: 1,
      public_label: "Twitter",
      normalized_url: "https://twitter.com/example",
    });
    expect(first.status).toBe(201);

    const duplicate = await addLink(started.id, {
      owner_user_id: owner,
      expected_revision: first.draft!.revision,
      public_label: "Twitter Again",
      normalized_url: "https://twitter.com/example",
    });
    expect(duplicate.status).toBe(400);
    expect(duplicate.error?.code).toBe("duplicate_link");
  });

  it("edits an existing link in place, preserving its id", async () => {
    const owner = "600000000000000007";
    const started = await startGlobalDraft(owner);
    await setOrientation(started.id, owner, 0, "domme");

    const added = await addLink(started.id, {
      owner_user_id: owner,
      expected_revision: 1,
      public_label: "Twitter",
      normalized_url: "https://twitter.com/example",
    });
    const linkId = added.draft!.document.links[0]!.id;

    const edited = await editLink(started.id, linkId, {
      owner_user_id: owner,
      expected_revision: added.draft!.revision,
      public_label: "Updated Label",
      normalized_url: "https://twitter.com/example",
      enabled: false,
    });
    expect(edited.status).toBe(200);
    expect(edited.draft?.document.links).toHaveLength(1);
    expect(edited.draft?.document.links[0]).toMatchObject({ id: linkId, public_label: "Updated Label", enabled: false });
  });

  it("marks a payment link preferred, and clearing it via delete nulls preferred_payment_link_id", async () => {
    const owner = "600000000000000008";
    const started = await startGlobalDraft(owner);
    await setOrientation(started.id, owner, 0, "domme");

    const added = await addLink(started.id, {
      owner_user_id: owner,
      expected_revision: 1,
      public_label: "CashApp",
      normalized_url: "https://cash.app/$example",
      preferred: true,
    });
    expect(added.status).toBe(201);
    const linkId = added.draft!.document.links[0]!.id;
    expect(added.draft?.document.preferred_payment_link_id).toBe(linkId);

    const removed = await deleteLink(started.id, linkId, { owner_user_id: owner, expected_revision: added.draft!.revision });
    expect(removed.status).toBe(200);
    expect(removed.draft?.document.links).toHaveLength(0);
    expect(removed.draft?.document.preferred_payment_link_id).toBeNull();
  });

  it("rejects marking a non-payment link as preferred", async () => {
    const owner = "600000000000000009";
    const started = await startGlobalDraft(owner);
    await setOrientation(started.id, owner, 0, "domme");

    const result = await addLink(started.id, {
      owner_user_id: owner,
      expected_revision: 1,
      public_label: "Twitter",
      normalized_url: "https://twitter.com/example",
      preferred: true,
    });
    expect(result.status).toBe(400);
    expect(result.error?.code).toBe("preferred_requires_payment");
  });

  it("returns 409 stale_revision for a mismatched expected_revision", async () => {
    const owner = "600000000000000010";
    const started = await startGlobalDraft(owner);
    await setOrientation(started.id, owner, 0, "domme");

    const result = await addLink(started.id, {
      owner_user_id: owner,
      expected_revision: 999,
      public_label: "Twitter",
      normalized_url: "https://twitter.com/example",
    });
    expect(result.status).toBe(409);
    expect(result.error?.code).toBe("stale_revision");
  });

  it("returns 400 unknown_link_id when editing/removing a link id that does not exist", async () => {
    const owner = "600000000000000011";
    const started = await startGlobalDraft(owner);
    await setOrientation(started.id, owner, 0, "domme");

    const edited = await editLink(started.id, "not-a-real-link-id", {
      owner_user_id: owner,
      expected_revision: 1,
      public_label: "X",
      normalized_url: "https://twitter.com/example",
    });
    expect(edited.status).toBe(400);
    expect(edited.error?.code).toBe("unknown_link_id");

    const removed = await deleteLink(started.id, "not-a-real-link-id", { owner_user_id: owner, expected_revision: 1 });
    expect(removed.status).toBe(400);
    expect(removed.error?.code).toBe("unknown_link_id");
  });
});
