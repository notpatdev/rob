import { env } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";
import {
  callWorker,
  generateThroneKeyPair,
  seedCreator,
  seedGuild,
  seedRegistration,
} from "./helpers";

interface KeyPair {
  publicKeyPem: string;
  sign: (timestamp: string, rawBody: string) => Promise<string>;
}

let keyPair: KeyPair;

beforeEach(async () => {
  keyPair = await generateThroneKeyPair();
  env.THRONE_PUBLIC_KEY_PEM = keyPair.publicKeyPem;
});

async function postWebhook(
  creatorId: string,
  secret: string,
  body: unknown,
  options?: { timestamp?: string; signatureHeader?: string; skipSignature?: boolean },
): Promise<Response> {
  const rawBody = JSON.stringify(body);
  const timestamp = options?.timestamp ?? String(Math.floor(Date.now() / 1000));
  const headers: Record<string, string> = { "content-type": "application/json" };
  if (!options?.skipSignature) {
    headers["X-Signature-Timestamp"] = timestamp;
    headers["X-Signature-Ed25519"] = options?.signatureHeader ?? (await keyPair.sign(timestamp, rawBody));
  }
  return callWorker(
    new Request(`https://worker.test/t/${creatorId}/${secret}`, {
      method: "POST",
      headers,
      body: rawBody,
    }),
  );
}

async function seedActiveCreatorAndGuild(prefix: string) {
  const guildId = `${prefix}0000000000000001`;
  await seedGuild(guildId);
  const creatorId = `creator-${prefix}`;
  const { secret } = await seedCreator({ id: creatorId, handle: prefix });
  await seedRegistration({ id: `reg-${prefix}`, guildId, creatorId });
  return { guildId, creatorId, secret };
}

describe("POST /t/:creatorId/:routeSecret", () => {
  it("returns 404 for an unknown creator", async () => {
    const response = await postWebhook("does-not-exist", "whatever", { type: "gift_purchased" });
    expect(response.status).toBe(404);
  });

  it("returns 404 for a wrong route secret", async () => {
    const { creatorId } = await seedActiveCreatorAndGuild("wrongsecret");
    const response = await postWebhook(creatorId, "totally-wrong-secret", { type: "gift_purchased" });
    expect(response.status).toBe(404);
  });

  it("requires signature headers", async () => {
    const { creatorId, secret } = await seedActiveCreatorAndGuild("nosig");
    const response = await postWebhook(creatorId, secret, { type: "gift_purchased" }, { skipSignature: true });
    expect(response.status).toBe(401);
  });

  it("rejects a stale timestamp outside the allowed skew", async () => {
    const { creatorId, secret } = await seedActiveCreatorAndGuild("staletime");
    const staleTimestamp = String(Math.floor(Date.now() / 1000) - 3600);
    const response = await postWebhook(
      creatorId,
      secret,
      { type: "gift_purchased" },
      { timestamp: staleTimestamp },
    );
    expect(response.status).toBe(401);
  });

  it("rejects an invalid signature", async () => {
    const { creatorId, secret } = await seedActiveCreatorAndGuild("badsig");
    const response = await postWebhook(
      creatorId,
      secret,
      { type: "gift_purchased" },
      { signatureHeader: "00".repeat(64) },
    );
    expect(response.status).toBe(401);
  });

  it("acknowledges and ignores unsupported event types without creating rows", async () => {
    const { creatorId, secret } = await seedActiveCreatorAndGuild("unsupported");
    const response = await postWebhook(creatorId, secret, { type: "subscription_renewed" });
    expect(response.status).toBe(200);
    const body = await response.json();
    expect((body as { data: { status: string } }).data.status).toBe("ignored");

    const count = await env.DB.prepare("SELECT COUNT(*) as n FROM throne_events WHERE creator_id = ?")
      .bind(creatorId)
      .first<{ n: number }>();
    expect(count?.n).toBe(0);
  });

  it("verifies but does not record explicit test events", async () => {
    const { creatorId, secret } = await seedActiveCreatorAndGuild("testevt");
    const response = await postWebhook(creatorId, secret, {
      type: "gift_purchased",
      test: true,
      amount: 5,
    });
    expect(response.status).toBe(200);
    const body = await response.json();
    expect((body as { data: { status: string; verified: boolean } }).data).toEqual({
      status: "test",
      verified: true,
    });

    const count = await env.DB.prepare("SELECT COUNT(*) as n FROM throne_events WHERE creator_id = ?")
      .bind(creatorId)
      .first<{ n: number }>();
    expect(count?.n).toBe(0);
  });

  it("verifies but does not record events from a configured test sender", async () => {
    const { creatorId, secret } = await seedActiveCreatorAndGuild("testsender");
    // THRONE_TEST_GIFTER_USERNAMES is configured to "test-gifter" in vitest.config.ts.
    const response = await postWebhook(creatorId, secret, {
      type: "gift_purchased",
      amount: 5,
      gifter: { username: "Test-Gifter" },
    });
    expect(response.status).toBe(200);
    const body = await response.json();
    expect((body as { data: { status: string } }).data.status).toBe("test");

    const count = await env.DB.prepare("SELECT COUNT(*) as n FROM throne_events WHERE creator_id = ?")
      .bind(creatorId)
      .first<{ n: number }>();
    expect(count?.n).toBe(0);
  });

  it("records a supported event and fans it out to every active guild registration", async () => {
    const guildA = "910000000000000001";
    const guildB = "910000000000000002";
    await seedGuild(guildA);
    await seedGuild(guildB);
    const creatorId = "creator-fanout";
    const { secret } = await seedCreator({ id: creatorId, handle: "fanout" });
    await seedRegistration({ id: "reg-fanout-a", guildId: guildA, creatorId });
    await seedRegistration({ id: "reg-fanout-b", guildId: guildB, creatorId });

    const response = await postWebhook(creatorId, secret, {
      type: "gift_purchased",
      id: "evt-fanout-1",
      amount: 1099,
      currency: "usd",
      gifter: { username: "supporter" },
      item: { name: "Coffee" },
      created_at: "2024-01-01T00:00:00Z",
    });
    expect(response.status).toBe(200);
    const body = (await response.json()) as { data: { status: string; sent_to_guilds: number } };
    expect(body.data.status).toBe("recorded");
    expect(body.data.sent_to_guilds).toBe(2);

    const sends = await env.DB.prepare(
      `SELECT s.guild_id FROM sends s
       JOIN throne_events e ON e.id = s.event_id
       WHERE e.creator_id = ? AND e.event_id = ?`,
    )
      .bind(creatorId, "evt-fanout-1")
      .all<{ guild_id: string }>();
    expect((sends.results ?? []).map((r) => r.guild_id).sort()).toEqual([guildA, guildB].sort());

    const notifications = await env.DB.prepare(
      `SELECT COUNT(*) as n FROM notifications n
       JOIN sends s ON s.id = n.send_id
       JOIN throne_events e ON e.id = s.event_id
       WHERE e.creator_id = ?`,
    )
      .bind(creatorId)
      .first<{ n: number }>();
    expect(notifications?.n).toBe(2);
  });

  it("is idempotent for a repeated event id and does not fan out twice", async () => {
    const { creatorId, secret } = await seedActiveCreatorAndGuild("dupe");
    const payload = {
      type: "gift_purchased",
      id: "evt-dupe-1",
      amount: 5,
      gifter: { username: "supporter" },
    };

    const first = await postWebhook(creatorId, secret, payload);
    expect(first.status).toBe(200);
    expect(((await first.json()) as { data: { status: string } }).data.status).toBe("recorded");

    const second = await postWebhook(creatorId, secret, payload);
    expect(second.status).toBe(200);
    expect(((await second.json()) as { data: { status: string } }).data.status).toBe("duplicate");

    const eventCount = await env.DB.prepare("SELECT COUNT(*) as n FROM throne_events WHERE creator_id = ?")
      .bind(creatorId)
      .first<{ n: number }>();
    expect(eventCount?.n).toBe(1);

    const sendCount = await env.DB.prepare(
      `SELECT COUNT(*) as n FROM sends s JOIN throne_events e ON e.id = s.event_id WHERE e.creator_id = ?`,
    )
      .bind(creatorId)
      .first<{ n: number }>();
    expect(sendCount?.n).toBe(1);
  });

  it("is idempotent based on the fallback hash when no event or order id is present", async () => {
    const { creatorId, secret } = await seedActiveCreatorAndGuild("fallback");
    const payload = { type: "gift_crowdfunded", amount: 5, gifter: { username: "supporter" } };

    const first = await postWebhook(creatorId, secret, payload);
    expect(((await first.json()) as { data: { status: string } }).data.status).toBe("recorded");

    const second = await postWebhook(creatorId, secret, payload);
    expect(((await second.json()) as { data: { status: string } }).data.status).toBe("duplicate");
  });

  it("hides amount and sender for private events, and only sender for anonymous events", async () => {
    const { creatorId, secret } = await seedActiveCreatorAndGuild("privacy");

    await postWebhook(creatorId, secret, {
      type: "gift_purchased",
      id: "evt-private",
      amount: 50,
      is_private: true,
      gifter: { username: "hidden-sender" },
    });
    const privateRow = await env.DB.prepare(
      "SELECT amount_minor, sender_username, is_private FROM throne_events WHERE creator_id = ? AND event_id = ?",
    )
      .bind(creatorId, "evt-private")
      .first<{ amount_minor: number; sender_username: string | null; is_private: number }>();
    expect(privateRow?.amount_minor).toBe(0);
    expect(privateRow?.sender_username).toBeNull();
    expect(privateRow?.is_private).toBe(1);

    await postWebhook(creatorId, secret, {
      type: "gift_purchased",
      id: "evt-anon",
      amount: 5000,
      is_anonymous: true,
      gifter: { username: "hidden-sender" },
    });
    const anonRow = await env.DB.prepare(
      "SELECT amount_minor, sender_username, is_anonymous FROM throne_events WHERE creator_id = ? AND event_id = ?",
    )
      .bind(creatorId, "evt-anon")
      .first<{ amount_minor: number; sender_username: string | null; is_anonymous: number }>();
    expect(anonRow?.amount_minor).toBe(5000);
    expect(anonRow?.sender_username).toBeNull();
    expect(anonRow?.is_anonymous).toBe(1);
  });

  it("marks the creator's webhook as verified after a test event, without requiring a real event first", async () => {
    const { creatorId, secret } = await seedActiveCreatorAndGuild("verifytest");

    const before = await env.DB.prepare("SELECT webhook_verified_at FROM throne_creators WHERE id = ?")
      .bind(creatorId)
      .first<{ webhook_verified_at: string | null }>();
    expect(before?.webhook_verified_at).toBeNull();

    const response = await postWebhook(creatorId, secret, { type: "gift_purchased", test: true, amount: 5 });
    expect(response.status).toBe(200);

    const after = await env.DB.prepare("SELECT webhook_verified_at FROM throne_creators WHERE id = ?")
      .bind(creatorId)
      .first<{ webhook_verified_at: string | null }>();
    expect(after?.webhook_verified_at).not.toBeNull();
  });

  it("marks the creator's webhook as verified after a real supported event is recorded", async () => {
    const { creatorId, secret } = await seedActiveCreatorAndGuild("verifyreal");

    const response = await postWebhook(creatorId, secret, {
      type: "gift_purchased",
      id: "evt-verify-real",
      amount: 500,
      gifter: { username: "supporter" },
    });
    expect(response.status).toBe(200);

    const row = await env.DB.prepare("SELECT webhook_verified_at FROM throne_creators WHERE id = ?")
      .bind(creatorId)
      .first<{ webhook_verified_at: string | null }>();
    expect(row?.webhook_verified_at).not.toBeNull();
  });

  it("also marks the creator's webhook as verified when a real event is a duplicate", async () => {
    const { creatorId, secret } = await seedActiveCreatorAndGuild("verifydupe");
    const payload = {
      type: "gift_purchased",
      id: "evt-verify-dupe",
      amount: 500,
      gifter: { username: "supporter" },
    };

    await postWebhook(creatorId, secret, payload);
    await env.DB.prepare("UPDATE throne_creators SET webhook_verified_at = NULL WHERE id = ?")
      .bind(creatorId)
      .run();

    const second = await postWebhook(creatorId, secret, payload);
    expect(((await second.json()) as { data: { status: string } }).data.status).toBe("duplicate");

    const row = await env.DB.prepare("SELECT webhook_verified_at FROM throne_creators WHERE id = ?")
      .bind(creatorId)
      .first<{ webhook_verified_at: string | null }>();
    expect(row?.webhook_verified_at).not.toBeNull();
  });
});
