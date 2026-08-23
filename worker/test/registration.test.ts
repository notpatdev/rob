import { fetchMock, env } from "cloudflare:test";
import { beforeAll, describe, expect, it } from "vitest";
import { authHeaders, callWorker, jsonRequest, readJson, seedGuild } from "./helpers";

const FIRESTORE_PATH = "/v1/projects/onlywish-9d17b/databases/(default)/documents:runQuery";

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

/** Mocks a document where the creator ID lives only in an `_id` field, as some legacy Throne documents do. */
function mockResolveWithIdField(documentId: string, publicCreatorId: string, handle: string): void {
  fetchMock
    .get("https://firestore.googleapis.com")
    .intercept({ method: "POST", path: FIRESTORE_PATH })
    .reply(200, [
      {
        document: {
          name: `projects/onlywish-9d17b/databases/(default)/documents/creators/${documentId}`,
          fields: { _id: { stringValue: publicCreatorId }, username: { stringValue: handle } },
        },
      },
    ]);
}

function mockResolveEmpty(): void {
  fetchMock.get("https://firestore.googleapis.com").intercept({ method: "POST", path: FIRESTORE_PATH }).reply(200, []);
}

beforeAll(() => {
  fetchMock.activate();
  fetchMock.disableNetConnect();
});

describe("POST /v1/guilds/:guildId/registrations/domme", () => {
  it("requires the guild to be configured first", async () => {
    const response = await callWorker(
      jsonRequest(
        "POST",
        "/v1/guilds/900000000000000001/registrations/domme",
        { discord_user_id: "1", throne: "alice", reset_webhook: false },
        authHeaders(),
      ),
    );
    expect(response.status).toBe(404);
    const body = await readJson<{ error: { code: string } }>(response);
    expect(body.error.code).toBe("guild_not_configured");
  });

  it("issues a webhook URL and stores only the secret hash for a new creator", async () => {
    await seedGuild("900000000000000010");
    mockResolve("creator-alice", "alice");

    const response = await callWorker(
      jsonRequest(
        "POST",
        "/v1/guilds/900000000000000010/registrations/domme",
        { discord_user_id: "1000000000000000001", throne: "https://throne.com/alice", reset_webhook: false },
        authHeaders(),
      ),
    );
    expect(response.status).toBe(200);
    const body = await readJson<{
      data: { creator_id: string; throne_handle: string; webhook_url: string | null; webhook_state: string };
    }>(response);
    expect(body.data.webhook_state).toBe("issued");
    expect(body.data.throne_handle).toBe("alice");
    expect(body.data.webhook_url).toMatch(
      new RegExp(`^https://usebill\\.dev/t/${body.data.creator_id}/[\\w-]+$`),
    );
  });

  it("links an existing creator to a second guild for the same owner without re-issuing the URL", async () => {
    await seedGuild("900000000000000020");
    await seedGuild("900000000000000021");
    mockResolve("creator-bob", "bob");

    const first = await callWorker(
      jsonRequest(
        "POST",
        "/v1/guilds/900000000000000020/registrations/domme",
        { discord_user_id: "2000000000000000002", throne: "bob", reset_webhook: false },
        authHeaders(),
      ),
    );
    const firstBody = await readJson<{ data: { creator_id: string } }>(first);

    mockResolve("creator-bob", "bob");
    const second = await callWorker(
      jsonRequest(
        "POST",
        "/v1/guilds/900000000000000021/registrations/domme",
        { discord_user_id: "2000000000000000002", throne: "bob", reset_webhook: false },
        authHeaders(),
      ),
    );
    expect(second.status).toBe(200);
    const secondBody = await readJson<{
      data: { creator_id: string; webhook_url: string | null; webhook_state: string };
    }>(second);
    expect(secondBody.data.creator_id).toBe(firstBody.data.creator_id);
    expect(secondBody.data.webhook_state).toBe("existing");
    expect(secondBody.data.webhook_url).toBeNull();
  });

  it("rotates the secret and returns a new URL when reset_webhook is true", async () => {
    await seedGuild("900000000000000030");
    mockResolve("creator-carol", "carol");

    const first = await callWorker(
      jsonRequest(
        "POST",
        "/v1/guilds/900000000000000030/registrations/domme",
        { discord_user_id: "3000000000000000003", throne: "carol", reset_webhook: false },
        authHeaders(),
      ),
    );
    const firstBody = await readJson<{ data: { webhook_url: string | null } }>(first);
    await env.DB.prepare(
      "UPDATE throne_creators SET webhook_verified_at = ? WHERE public_creator_id = ?",
    )
      .bind("2026-08-23T12:00:00Z", "creator-carol")
      .run();

    mockResolve("creator-carol", "carol");
    const second = await callWorker(
      jsonRequest(
        "POST",
        "/v1/guilds/900000000000000030/registrations/domme",
        { discord_user_id: "3000000000000000003", throne: "carol", reset_webhook: true },
        authHeaders(),
      ),
    );
    expect(second.status).toBe(200);
    const secondBody = await readJson<{ data: { webhook_url: string | null; webhook_state: string } }>(second);
    expect(secondBody.data.webhook_state).toBe("rotated");
    expect(secondBody.data.webhook_url).not.toBeNull();
    expect(secondBody.data.webhook_url).not.toBe(firstBody.data.webhook_url);
    expect(
      await env.DB.prepare(
        "SELECT webhook_verified_at FROM throne_creators WHERE public_creator_id = ?",
      )
        .bind("creator-carol")
        .first(),
    ).toEqual({ webhook_verified_at: null });
  });

  it("rejects linking an already-owned creator to a different Discord user", async () => {
    await seedGuild("900000000000000040");
    await seedGuild("900000000000000041");
    mockResolve("creator-dana", "dana");

    await callWorker(
      jsonRequest(
        "POST",
        "/v1/guilds/900000000000000040/registrations/domme",
        { discord_user_id: "4000000000000000004", throne: "dana", reset_webhook: false },
        authHeaders(),
      ),
    );

    mockResolve("creator-dana", "dana");
    const conflict = await callWorker(
      jsonRequest(
        "POST",
        "/v1/guilds/900000000000000041/registrations/domme",
        { discord_user_id: "5000000000000000005", throne: "dana", reset_webhook: false },
        authHeaders(),
      ),
    );
    expect(conflict.status).toBe(409);
    const body = await readJson<{ error: { code: string } }>(conflict);
    expect(body.error.code).toBe("creator_owned");
  });

  it("returns not found when the Throne creator cannot be resolved", async () => {
    await seedGuild("900000000000000050");
    mockResolveEmpty();

    const response = await callWorker(
      jsonRequest(
        "POST",
        "/v1/guilds/900000000000000050/registrations/domme",
        { discord_user_id: "6000000000000000006", throne: "ghost", reset_webhook: false },
        authHeaders(),
      ),
    );
    expect(response.status).toBe(404);
    const body = await readJson<{ error: { code: string } }>(response);
    expect(body.error.code).toBe("throne_creator_not_found");
  });

  it("rejects invalid snowflakes and empty throne input", async () => {
    await seedGuild("900000000000000060");

    const badDiscordId = await callWorker(
      jsonRequest(
        "POST",
        "/v1/guilds/900000000000000060/registrations/domme",
        { discord_user_id: "not-a-snowflake", throne: "eve", reset_webhook: false },
        authHeaders(),
      ),
    );
    expect(badDiscordId.status).toBe(400);

    const emptyThrone = await callWorker(
      jsonRequest(
        "POST",
        "/v1/guilds/900000000000000060/registrations/domme",
        { discord_user_id: "7000000000000000007", throne: "   ", reset_webhook: false },
        authHeaders(),
      ),
    );
    expect(emptyThrone.status).toBe(400);
  });

  it("queries Firestore's public creators collection by exact username field path", async () => {
    await seedGuild("900000000000000070");

    let capturedBody: unknown = null;
    fetchMock
      .get("https://firestore.googleapis.com")
      .intercept({ method: "POST", path: FIRESTORE_PATH })
      .reply(200, (opts) => {
        capturedBody = JSON.parse(opts.body as string);
        return [
          {
            document: {
              name: "projects/onlywish-9d17b/databases/(default)/documents/creators/creator-frank",
              fields: { username: { stringValue: "frank" } },
            },
          },
        ];
      });

    const response = await callWorker(
      jsonRequest(
        "POST",
        "/v1/guilds/900000000000000070/registrations/domme",
        { discord_user_id: "8000000000000000008", throne: "frank", reset_webhook: false },
        authHeaders(),
      ),
    );
    expect(response.status).toBe(200);
    expect(capturedBody).toEqual({
      structuredQuery: {
        from: [{ collectionId: "creators" }],
        where: {
          fieldFilter: {
            field: { fieldPath: "username" },
            op: "EQUAL",
            value: { stringValue: "frank" },
          },
        },
        limit: 1,
      },
    });
  });

  it("resolves the creator ID from an `_id` field when present, ignoring the document name", async () => {
    await seedGuild("900000000000000080");
    mockResolveWithIdField("some-internal-doc-id", "creator-grace-public-id", "grace");

    const response = await callWorker(
      jsonRequest(
        "POST",
        "/v1/guilds/900000000000000080/registrations/domme",
        { discord_user_id: "9000000000000000009", throne: "grace", reset_webhook: false },
        authHeaders(),
      ),
    );
    expect(response.status).toBe(200);
    const body = await readJson<{ data: { creator_id: string; webhook_url: string | null } }>(response);
    expect(body.data.webhook_url).toMatch(new RegExp(`^https://usebill\\.dev/t/${body.data.creator_id}/[\\w-]+$`));
  });

  it("updates the existing registration in place when the same Discord user re-registers in a guild", async () => {
    await seedGuild("900000000000000090");
    mockResolve("creator-henry", "henry");

    const first = await callWorker(
      jsonRequest(
        "POST",
        "/v1/guilds/900000000000000090/registrations/domme",
        { discord_user_id: "1100000000000000011", throne: "henry", reset_webhook: false },
        authHeaders(),
      ),
    );
    expect(first.status).toBe(200);
    const firstBody = await readJson<{ data: { creator_id: string } }>(first);

    mockResolve("creator-irene", "irene");
    const second = await callWorker(
      jsonRequest(
        "POST",
        "/v1/guilds/900000000000000090/registrations/domme",
        { discord_user_id: "1100000000000000011", throne: "irene", reset_webhook: false },
        authHeaders(),
      ),
    );
    expect(second.status).toBe(200);
    const secondBody = await readJson<{ data: { creator_id: string } }>(second);
    expect(secondBody.data.creator_id).not.toBe(firstBody.data.creator_id);

    const rows = await env.DB.prepare(
      "SELECT creator_id FROM domme_registrations WHERE guild_id = ? AND discord_user_id = ?",
    )
      .bind("900000000000000090", "1100000000000000011")
      .all<{ creator_id: string }>();
    expect(rows.results).toHaveLength(1);
    expect(rows.results?.[0]?.creator_id).toBe(secondBody.data.creator_id);
  });
});
