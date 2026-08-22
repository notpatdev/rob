import { describe, expect, it } from "vitest";
import { env, fetchMock } from "cloudflare:test";
import { authHeaders, callWorker, jsonRequest, readJson, TEST_HOME_GUILD_ID } from "./helpers";
import { createLinkImport, confirmLinkImport } from "../src/profile/linkImportService";
import type { ImporterDeps } from "../src/profile/importer/fetchSafely";

interface DraftEnvelope {
  data: { draft: { id: string; revision: number } };
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

const PUBLIC_IP = "93.184.216.34";

function depsFor(html: string): ImporterDeps {
  return {
    fetchImpl: (async () => new Response(html, { status: 200, headers: { "content-type": "text/html; charset=utf-8" } })) as typeof fetch,
    resolveIps: async () => [PUBLIC_IP],
  };
}

describe("link import service (direct, injected deps)", () => {
  it("creates an import with normalized/classified candidates", async () => {
    const owner = "700000000000000001";
    const started = await startGlobalDraft(owner);
    await setOrientation(started.id, owner, 0, "domme");

    const html = `<a href="https://twitter.com/example">Twitter</a><a href="https://cash.app/$example">Tip</a>`;
    const created = await createLinkImport(
      env,
      { draftId: started.id, ownerUserId: owner, expectedRevision: 1, sourceUrl: "https://linkpage.example/me" },
      depsFor(html),
    );
    const importContract = created.importContract;
    expect(importContract.status).toBe("ready");
    expect(importContract.provider).toBe("generic");
    expect(importContract.candidates).toHaveLength(2);
    expect(importContract.candidates.every((c) => c.selected)).toBe(true);
    expect(created.draft.revision).toBe(2);
  });

  it("records a blocked import (no candidates) for an SSRF-violating source URL, without failing the request", async () => {
    const owner = "700000000000000002";
    const started = await startGlobalDraft(owner);
    await setOrientation(started.id, owner, 0, "domme");

    const created = await createLinkImport(
      env,
      { draftId: started.id, ownerUserId: owner, expectedRevision: 1, sourceUrl: "https://10.0.0.5/" },
      { fetchImpl: (async () => new Response("")) as typeof fetch, resolveIps: async () => [] },
    );
    const importContract = created.importContract;
    expect(importContract.status).toBe("blocked");
    expect(importContract.candidates).toEqual([]);
    expect(created.draft.revision).toBe(2);
  });

  it("confirms an import: promotes selected candidates into the draft's own links atomically", async () => {
    const owner = "700000000000000003";
    const started = await startGlobalDraft(owner);
    await setOrientation(started.id, owner, 0, "domme");

    const html = `<a href="https://twitter.com/example">Twitter</a><a href="https://cash.app/$example">Tip</a>`;
    const created = await createLinkImport(
      env,
      { draftId: started.id, ownerUserId: owner, expectedRevision: 1, sourceUrl: "https://linkpage.example/me" },
      depsFor(html),
    );
    const importContract = created.importContract;

    const confirmed = await confirmLinkImport(env, {
      draftId: started.id,
      importId: importContract.id,
      ownerUserId: owner,
      expectedRevision: created.draft.revision,
      candidateIds: null,
    });
    expect(confirmed.addedLinkCount).toBe(2);
    expect(confirmed.skippedDuplicateCount).toBe(0);
    expect(confirmed.draft.document.links).toHaveLength(2);
    expect(confirmed.draft.revision).toBe(3);
  });

  it("confirming only a subset of candidate ids promotes just those", async () => {
    const owner = "700000000000000004";
    const started = await startGlobalDraft(owner);
    await setOrientation(started.id, owner, 0, "domme");

    const html = `<a href="https://twitter.com/example">Twitter</a><a href="https://cash.app/$example">Tip</a>`;
    const created = await createLinkImport(
      env,
      { draftId: started.id, ownerUserId: owner, expectedRevision: 1, sourceUrl: "https://linkpage.example/me" },
      depsFor(html),
    );
    const importContract = created.importContract;
    const twitterCandidateId = importContract.candidates.find((c) => c.platform === "twitter")!.id;

    const confirmed = await confirmLinkImport(env, {
      draftId: started.id,
      importId: importContract.id,
      ownerUserId: owner,
      expectedRevision: created.draft.revision,
      candidateIds: [twitterCandidateId],
    });
    expect(confirmed.addedLinkCount).toBe(1);
    expect(confirmed.draft.document.links).toHaveLength(1);
    expect(confirmed.draft.document.links[0]?.platform).toBe("twitter");
  });

  it("skips a candidate that duplicates an already-existing link", async () => {
    const owner = "700000000000000005";
    const started = await startGlobalDraft(owner);
    await setOrientation(started.id, owner, 0, "domme");

    // Manually add a link first via the manual-CRUD route.
    const addResponse = await callWorker(
      jsonRequest(
        "POST",
        `/v1/profile-drafts/${started.id}/links`,
        { owner_user_id: owner, expected_revision: 1, public_label: "Twitter", normalized_url: "https://twitter.com/example" },
        authHeaders(),
      ),
    );
    const added = await readJson<DraftEnvelope>(addResponse);

    const html = `<a href="https://twitter.com/example">Twitter</a><a href="https://cash.app/$example">Tip</a>`;
    const created = await createLinkImport(
      env,
      { draftId: started.id, ownerUserId: owner, expectedRevision: added.data.draft.revision, sourceUrl: "https://linkpage.example/me" },
      depsFor(html),
    );
    const importContract = created.importContract;

    const confirmed = await confirmLinkImport(env, {
      draftId: started.id,
      importId: importContract.id,
      ownerUserId: owner,
      expectedRevision: created.draft.revision,
      candidateIds: null,
    });
    expect(confirmed.addedLinkCount).toBe(1);
    expect(confirmed.skippedDuplicateCount).toBe(1);
    expect(confirmed.draft.document.links).toHaveLength(2);
  });

  it("filters payment candidates for a submissive profile", async () => {
    const owner = "700000000000000006";
    const started = await startGlobalDraft(owner);
    await setOrientation(started.id, owner, 0, "submissive");

    const created = await createLinkImport(
      env,
      {
        draftId: started.id,
        ownerUserId: owner,
        expectedRevision: 1,
        sourceUrl: "https://linkpage.example/me",
      },
      depsFor(
        `<a href="https://twitter.com/example">Twitter</a><a href="https://cash.app/$example">Tip</a>`,
      ),
    );

    expect(created.importContract.candidates).toEqual([
      expect.objectContaining({ platform: "twitter", linkType: "social" }),
    ]);
  });

  it("revalidates stored candidates before promoting them to public links", async () => {
    const owner = "700000000000000007";
    const started = await startGlobalDraft(owner);
    await setOrientation(started.id, owner, 0, "domme");
    const created = await createLinkImport(
      env,
      {
        draftId: started.id,
        ownerUserId: owner,
        expectedRevision: 1,
        sourceUrl: "https://linkpage.example/me",
      },
      depsFor(`<a href="https://twitter.com/example">Twitter</a>`),
    );
    await env.DB.prepare(
      "UPDATE profile_link_import_candidates SET normalized_url = ? WHERE import_id = ?",
    )
      .bind("https://user:password@example.com/private", created.importContract.id)
      .run();

    await expect(
      confirmLinkImport(env, {
        draftId: started.id,
        importId: created.importContract.id,
        ownerUserId: owner,
        expectedRevision: created.draft.revision,
        candidateIds: null,
      }),
    ).rejects.toThrowError(expect.objectContaining({ code: "invalid_url_credentials" }));
  });
});

describe("link import HTTP routes (real fetch wiring via fetchMock)", () => {
  it("fetches and stores candidates through the actual route, hitting DNS-over-HTTPS and the target host", async () => {
    fetchMock.activate();
    fetchMock.disableNetConnect();

    const owner = "700000000000000010";
    const started = await startGlobalDraft(owner);
    await setOrientation(started.id, owner, 0, "domme");

    fetchMock
      .get("https://cloudflare-dns.com")
      .intercept({ method: "GET", path: /\/dns-query\?name=mypage\.example&type=A/ })
      .reply(200, { Status: 0, Answer: [{ type: 1, data: "93.184.216.34" }] }, { headers: { "content-type": "application/dns-json" } });
    fetchMock
      .get("https://cloudflare-dns.com")
      .intercept({ method: "GET", path: /\/dns-query\?name=mypage\.example&type=AAAA/ })
      .reply(200, { Status: 0 }, { headers: { "content-type": "application/dns-json" } });
    fetchMock
      .get("https://mypage.example")
      .intercept({ method: "GET", path: "/" })
      .reply(200, `<a href="https://twitter.com/example">Twitter</a>`, { headers: { "content-type": "text/html" } });

    const response = await callWorker(
      jsonRequest(
        "POST",
        `/v1/profile-drafts/${started.id}/link-imports`,
        { owner_user_id: owner, expected_revision: 1, source_url: "https://mypage.example/" },
        authHeaders(),
      ),
    );
    expect(response.status).toBe(201);
    const body = await readJson<{
      data: {
        import: { status: string; candidates: { platform: string }[] };
        draft: { revision: number };
      };
    }>(response);
    expect(body.data.import.status).toBe("ready");
    expect(body.data.import.candidates).toEqual([expect.objectContaining({ platform: "twitter" })]);
    expect(body.data.draft.revision).toBe(2);
  });
});
