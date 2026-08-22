import { env } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import { callWorker, jsonRequest, readJson, seedCreator, seedGuild, seedNotificationChain } from "./helpers";

const BOT_TOKEN = "test-bot-token";
function authed(headers?: Record<string, string>): Record<string, string> {
  return { Authorization: `Bearer ${BOT_TOKEN}`, ...headers };
}

async function lease(body: Record<string, unknown>) {
  return callWorker(jsonRequest("POST", "/v1/notifications/lease", body, authed()));
}

describe("POST /v1/notifications/lease", () => {
  it("leases pending notifications and returns the full render payload", async () => {
    const guildId = "920000000000000001";
    await seedGuild(guildId, "930000000000000001");
    const { id: creatorId } = await seedCreator({ id: "creator-lease-1", handle: "leasey" });
    const { notificationId, sendId } = await seedNotificationChain({
      guildId,
      creatorId,
      recipientUserId: "940000000000000001",
      senderUsername: "supporter1",
      amountMinor: 1099,
      currency: "USD",
      itemName: "Coffee",
    });

    const response = await lease({ owner: "worker-a", limit: 10, lease_seconds: 60 });
    expect(response.status).toBe(200);
    const body = await readJson<{ data: { notifications: Array<Record<string, unknown>> } }>(response);
    expect(body.data.notifications).toHaveLength(1);
    const row = body.data.notifications[0]!;
    expect(row.notification_id).toBe(notificationId);
    expect(row.send_id).toBe(sendId);
    expect(row.guild_id).toBe(guildId);
    expect(row.channel_id).toBe("930000000000000001");
    expect(row.recipient_user_id).toBe("940000000000000001");
    expect(row.throne_handle).toBe("leasey");
    expect(row.sender_name).toBe("supporter1");
    expect(row.amount_minor).toBe(1099);
    expect(row.currency).toBe("USD");
    expect(row.is_private).toBe(false);
    expect(row.is_anonymous).toBe(false);
    expect(row.item_name).toBe("Coffee");
    expect(typeof row.purchased_at).toBe("string");
    expect(typeof row.lease_token).toBe("string");
    // A fresh, never-attempted pending notification can never have already
    // reached Discord, so callers can skip the duplicate-message scan.
    expect(row.delivery_may_exist).toBe(false);
    // All IDs must be plain strings, never coerced to numbers.
    expect(typeof row.guild_id).toBe("string");
    expect(typeof row.channel_id).toBe("string");
    expect(typeof row.recipient_user_id).toBe("string");
  });

  it("does not lease the same row to two concurrent callers", async () => {
    const guildId = "920000000000000002";
    await seedGuild(guildId);
    const { id: creatorId } = await seedCreator({ id: "creator-lease-2" });
    await seedNotificationChain({ guildId, creatorId });

    const [a, b] = await Promise.all([
      lease({ owner: "worker-a", limit: 5, lease_seconds: 60 }),
      lease({ owner: "worker-b", limit: 5, lease_seconds: 60 }),
    ]);
    const [bodyA, bodyB] = await Promise.all([
      readJson<{ data: { notifications: unknown[] } }>(a),
      readJson<{ data: { notifications: unknown[] } }>(b),
    ]);
    const total = bodyA.data.notifications.length + bodyB.data.notifications.length;
    expect(total).toBe(1);
  });

  it("does not re-lease a row whose lease has not expired", async () => {
    const guildId = "920000000000000003";
    await seedGuild(guildId);
    const { id: creatorId } = await seedCreator({ id: "creator-lease-3" });
    const future = new Date(Date.now() + 60_000).toISOString();
    await seedNotificationChain({
      guildId,
      creatorId,
      status: "leased",
      leaseToken: "existing-token",
      leaseOwner: "worker-a",
      leaseExpiresAt: future,
    });

    const response = await lease({ owner: "worker-b", limit: 5, lease_seconds: 60 });
    const body = await readJson<{ data: { notifications: unknown[] } }>(response);
    expect(body.data.notifications).toHaveLength(0);
  });

  it("reclaims a row whose lease has already expired", async () => {
    const guildId = "920000000000000004";
    await seedGuild(guildId);
    const { id: creatorId } = await seedCreator({ id: "creator-lease-4" });
    const past = new Date(Date.now() - 60_000).toISOString();
    const { notificationId } = await seedNotificationChain({
      guildId,
      creatorId,
      status: "leased",
      leaseToken: "expired-token",
      leaseOwner: "worker-a",
      leaseExpiresAt: past,
    });

    const response = await lease({ owner: "worker-b", limit: 5, lease_seconds: 60 });
    const body = await readJson<{
      data: { notifications: Array<{ notification_id: string; lease_token: string; delivery_may_exist: boolean }> };
    }>(response);
    expect(body.data.notifications).toHaveLength(1);
    expect(body.data.notifications[0]!.notification_id).toBe(notificationId);
    expect(body.data.notifications[0]!.lease_token).not.toBe("expired-token");
    // Reclaimed from an expired `leased` row: the prior owner could have
    // crashed after posting to Discord but before acking, so a duplicate
    // message may already exist and must be scanned for.
    expect(body.data.notifications[0]!.delivery_may_exist).toBe(true);
  });

  it("flags delivery_may_exist for a row re-leased after a prior nack", async () => {
    const guildId = "920000000000000011";
    await seedGuild(guildId);
    const { id: creatorId } = await seedCreator({ id: "creator-lease-5" });
    await seedNotificationChain({ guildId, creatorId, maxAttempts: 5 });

    const firstLease = await lease({ owner: "worker-a", limit: 5, lease_seconds: 60 });
    const firstLeaseBody = await readJson<{
      data: { notifications: Array<{ notification_id: string; lease_token: string }> };
    }>(firstLease);
    const { notification_id, lease_token } = firstLeaseBody.data.notifications[0]!;

    await callWorker(
      jsonRequest(
        "POST",
        `/v1/notifications/${notification_id}/nack`,
        { lease_token, error: "transient", permanent: false },
        authed(),
      ),
    );

    // Force the retry to be immediately due so the re-lease can claim it.
    await env.DB.prepare("UPDATE notifications SET next_attempt_at = ? WHERE id = ?")
      .bind(new Date(Date.now() - 1000).toISOString(), notification_id)
      .run();

    const secondLease = await lease({ owner: "worker-b", limit: 5, lease_seconds: 60 });
    const secondLeaseBody = await readJson<{
      data: { notifications: Array<{ notification_id: string; delivery_may_exist: boolean }> };
    }>(secondLease);
    expect(secondLeaseBody.data.notifications).toHaveLength(1);
    expect(secondLeaseBody.data.notifications[0]!.notification_id).toBe(notification_id);
    // Already-retried rows (attempts > 0) may have partially succeeded on a
    // prior attempt, so callers must scan for a duplicate before posting.
    expect(secondLeaseBody.data.notifications[0]!.delivery_may_exist).toBe(true);
  });

  it("rejects malformed lease requests", async () => {
    expect((await lease({ owner: "", limit: 1, lease_seconds: 60 })).status).toBe(400);
    expect((await lease({ owner: "a", limit: 0, lease_seconds: 60 })).status).toBe(400);
    expect((await lease({ owner: "a", limit: 1, lease_seconds: 0 })).status).toBe(400);
  });

  it("requires bearer auth", async () => {
    const response = await callWorker(
      jsonRequest("POST", "/v1/notifications/lease", { owner: "a", limit: 1, lease_seconds: 60 }),
    );
    expect(response.status).toBe(401);
  });
});

describe("POST /v1/notifications/:id/ack", () => {
  it("acknowledges a leased notification with the discord message id", async () => {
    const guildId = "920000000000000005";
    await seedGuild(guildId);
    const { id: creatorId } = await seedCreator({ id: "creator-ack-1" });
    await seedNotificationChain({ guildId, creatorId });

    const leaseResponse = await lease({ owner: "worker-a", limit: 5, lease_seconds: 60 });
    const leaseBody = await readJson<{ data: { notifications: Array<{ notification_id: string; lease_token: string }> } }>(
      leaseResponse,
    );
    const { notification_id, lease_token } = leaseBody.data.notifications[0]!;

    const ackResponse = await callWorker(
      jsonRequest(
        "POST",
        `/v1/notifications/${notification_id}/ack`,
        { lease_token, discord_message_id: "950000000000000001" },
        authed(),
      ),
    );
    expect(ackResponse.status).toBe(200);
    const ackBody = await readJson<{ data: { status: string } }>(ackResponse);
    expect(ackBody.data.status).toBe("acked");

    const row = await env.DB.prepare("SELECT status, message_id FROM notifications WHERE id = ?")
      .bind(notification_id)
      .first<{ status: string; message_id: string }>();
    expect(row?.status).toBe("acked");
    expect(row?.message_id).toBe("950000000000000001");
  });

  it("rejects ack with the wrong lease token", async () => {
    const guildId = "920000000000000006";
    await seedGuild(guildId);
    const { id: creatorId } = await seedCreator({ id: "creator-ack-2" });
    await seedNotificationChain({ guildId, creatorId });
    const leaseResponse = await lease({ owner: "worker-a", limit: 5, lease_seconds: 60 });
    const leaseBody = await readJson<{ data: { notifications: Array<{ notification_id: string }> } }>(leaseResponse);
    const { notification_id } = leaseBody.data.notifications[0]!;

    const ackResponse = await callWorker(
      jsonRequest(
        "POST",
        `/v1/notifications/${notification_id}/ack`,
        { lease_token: "wrong-token", discord_message_id: "950000000000000002" },
        authed(),
      ),
    );
    expect(ackResponse.status).toBe(409);
  });

  it("rejects ack against an unknown notification id", async () => {
    const response = await callWorker(
      jsonRequest(
        "POST",
        "/v1/notifications/does-not-exist/ack",
        { lease_token: "x", discord_message_id: "950000000000000003" },
        authed(),
      ),
    );
    expect(response.status).toBe(404);
  });

  it("rejects an expired lease", async () => {
    const guildId = "920000000000000007";
    await seedGuild(guildId);
    const { id: creatorId } = await seedCreator({ id: "creator-ack-3" });
    const past = new Date(Date.now() - 1000).toISOString();
    const { notificationId } = await seedNotificationChain({
      guildId,
      creatorId,
      status: "leased",
      leaseToken: "stale-token",
      leaseOwner: "worker-a",
      leaseExpiresAt: past,
    });

    const response = await callWorker(
      jsonRequest(
        "POST",
        `/v1/notifications/${notificationId}/ack`,
        { lease_token: "stale-token", discord_message_id: "950000000000000004" },
        authed(),
      ),
    );
    expect(response.status).toBe(409);
  });
});

describe("POST /v1/notifications/:id/nack", () => {
  it("schedules a bounded backoff retry for a transient failure", async () => {
    const guildId = "920000000000000008";
    await seedGuild(guildId);
    const { id: creatorId } = await seedCreator({ id: "creator-nack-1" });
    await seedNotificationChain({ guildId, creatorId, maxAttempts: 5 });
    const leaseResponse = await lease({ owner: "worker-a", limit: 5, lease_seconds: 60 });
    const leaseBody = await readJson<{ data: { notifications: Array<{ notification_id: string; lease_token: string }> } }>(
      leaseResponse,
    );
    const { notification_id, lease_token } = leaseBody.data.notifications[0]!;

    const beforeNack = Date.now();
    const nackResponse = await callWorker(
      jsonRequest(
        "POST",
        `/v1/notifications/${notification_id}/nack`,
        { lease_token, error: "discord 500", permanent: false },
        authed(),
      ),
    );
    expect(nackResponse.status).toBe(200);
    const nackBody = await readJson<{ data: { status: string; attempts: number } }>(nackResponse);
    expect(nackBody.data.status).toBe("pending");
    expect(nackBody.data.attempts).toBe(1);

    const row = await env.DB.prepare(
      "SELECT status, attempts, next_attempt_at, lease_token FROM notifications WHERE id = ?",
    )
      .bind(notification_id)
      .first<{ status: string; attempts: number; next_attempt_at: string; lease_token: string | null }>();
    expect(row?.status).toBe("pending");
    expect(row?.attempts).toBe(1);
    expect(row?.lease_token).toBeNull();
    // The retry must be scheduled in the future (bounded backoff), not immediately re-leasable.
    expect(new Date(row!.next_attempt_at).getTime()).toBeGreaterThan(beforeNack);

    const release = await lease({ owner: "worker-b", limit: 5, lease_seconds: 60 });
    const releaseBody = await readJson<{ data: { notifications: unknown[] } }>(release);
    expect(releaseBody.data.notifications).toHaveLength(0);
  });

  it("moves straight to dead_letter on a permanent nack", async () => {
    const guildId = "920000000000000009";
    await seedGuild(guildId);
    const { id: creatorId } = await seedCreator({ id: "creator-nack-2" });
    await seedNotificationChain({ guildId, creatorId, maxAttempts: 5 });
    const leaseResponse = await lease({ owner: "worker-a", limit: 5, lease_seconds: 60 });
    const leaseBody = await readJson<{ data: { notifications: Array<{ notification_id: string; lease_token: string }> } }>(
      leaseResponse,
    );
    const { notification_id, lease_token } = leaseBody.data.notifications[0]!;

    const nackResponse = await callWorker(
      jsonRequest(
        "POST",
        `/v1/notifications/${notification_id}/nack`,
        { lease_token, error: "unrecoverable", permanent: true },
        authed(),
      ),
    );
    const nackBody = await readJson<{ data: { status: string } }>(nackResponse);
    expect(nackBody.data.status).toBe("dead_letter");

    const row = await env.DB.prepare("SELECT status FROM notifications WHERE id = ?")
      .bind(notification_id)
      .first<{ status: string }>();
    expect(row?.status).toBe("dead_letter");
  });

  it("dead-letters once max_attempts is reached even without a permanent flag", async () => {
    const guildId = "920000000000000010";
    await seedGuild(guildId);
    const { id: creatorId } = await seedCreator({ id: "creator-nack-3" });
    await seedNotificationChain({ guildId, creatorId, maxAttempts: 1 });
    const leaseResponse = await lease({ owner: "worker-a", limit: 5, lease_seconds: 60 });
    const leaseBody = await readJson<{ data: { notifications: Array<{ notification_id: string; lease_token: string }> } }>(
      leaseResponse,
    );
    const { notification_id, lease_token } = leaseBody.data.notifications[0]!;

    const nackResponse = await callWorker(
      jsonRequest(
        "POST",
        `/v1/notifications/${notification_id}/nack`,
        { lease_token, error: "still failing", permanent: false },
        authed(),
      ),
    );
    const nackBody = await readJson<{ data: { status: string } }>(nackResponse);
    expect(nackBody.data.status).toBe("dead_letter");
  });
});
