import type { RouteContext } from "../router.js";
import { resolveConfig, type Env } from "../env.js";
import { Errors, ok } from "../util/response.js";
import { constantTimeEqualHex, sha256Hex } from "../util/hash.js";
import { newId, nowIso } from "../util/id.js";
import { parseThroneEvent } from "../throne/parser.js";
import { importThroneEd25519PublicKey, isTimestampWithinSkew, verifyThroneSignature } from "../throne/signature.js";
import { resolveSenderDiscordUserId } from "../profile/aliasAttribution.js";

interface CreatorRow {
  id: string;
  route_secret_hash: string;
}

interface RegistrationRow {
  id: string;
  guild_id: string;
}

function isUniqueConstraintError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  return /UNIQUE constraint failed/i.test(message);
}

export function webhookVerificationStatement(
  env: Env,
  creatorId: string,
  authenticatedRouteSecretHash: string,
  verifiedAt: string,
): D1PreparedStatement {
  return env.DB.prepare(
    "UPDATE throne_creators SET webhook_verified_at = ? WHERE id = ? AND route_secret_hash = ?",
  ).bind(verifiedAt, creatorId, authenticatedRouteSecretHash);
}

export async function handleThroneWebhook(ctx: RouteContext): Promise<Response> {
  const creatorId = ctx.params.creatorId ?? "";
  const routeSecret = ctx.params.routeSecret ?? "";
  if (!creatorId || !routeSecret) return Errors.notFound("Route not found");

  const creator = await ctx.env.DB.prepare("SELECT id, route_secret_hash FROM throne_creators WHERE id = ?")
    .bind(creatorId)
    .first<CreatorRow>();
  // Return an identical 404 whether the creator is unknown or the secret is
  // wrong, so this endpoint cannot be used as an existence oracle.
  if (!creator) return Errors.notFound("Route not found");

  const presentedHash = await sha256Hex(routeSecret);
  if (!(await constantTimeEqualHex(presentedHash, creator.route_secret_hash))) {
    return Errors.notFound("Route not found");
  }

  const timestampHeader = ctx.request.headers.get("X-Signature-Timestamp");
  const signatureHeader = ctx.request.headers.get("X-Signature-Ed25519");
  if (!timestampHeader || !signatureHeader) {
    return Errors.unauthorized("Missing signature headers");
  }

  const config = resolveConfig(ctx.env);
  if (!isTimestampWithinSkew(timestampHeader, config.maxTimestampSkewSeconds)) {
    return Errors.unauthorized("Signature timestamp is out of range");
  }

  // The raw body is captured and verified before any JSON parsing occurs.
  const rawBody = await ctx.request.text();

  const publicKey = await importThroneEd25519PublicKey(ctx.env.THRONE_PUBLIC_KEY_PEM);
  if (!publicKey) {
    console.error("THRONE_PUBLIC_KEY_PEM is not configured or is invalid");
    return Errors.internal("Webhook signing key is not configured");
  }

  const verified = await verifyThroneSignature({
    publicKey,
    timestamp: timestampHeader,
    rawBody,
    signatureHeader,
  });
  if (!verified) return Errors.unauthorized("Invalid signature");

  let payload: unknown;
  try {
    payload = JSON.parse(rawBody);
  } catch {
    return Errors.badRequest("Invalid JSON body", "invalid_body");
  }

  const parsed = await parseThroneEvent(payload);

  if (parsed.normalizedType === null) {
    // Unsupported event types are acknowledged and ignored.
    return ok({ status: "ignored", reason: "unsupported_event_type" });
  }

  const isKnownTestSender =
    parsed.rawSenderUsername !== null &&
    config.testGifterUsernames.has(parsed.rawSenderUsername.toLowerCase());
  if (parsed.isTest || isKnownTestSender) {
    // Explicit test events (and configured test senders) verify the webhook
    // but never create an event, send, or notification.
    const verification = await webhookVerificationStatement(
      ctx.env,
      creatorId,
      presentedHash,
      nowIso(),
    ).run();
    if (verification.meta.changes !== 1) {
      return Errors.notFound("Route not found");
    }
    return ok({ status: "test", verified: true });
  }

  const registrations = await ctx.env.DB.prepare(
    "SELECT id, guild_id FROM domme_registrations WHERE creator_id = ? AND active = 1",
  )
    .bind(creatorId)
    .all<RegistrationRow>();
  const activeRegistrations: RegistrationRow[] = registrations.results ?? [];

  const eventRowId = newId();
  const receivedAt = nowIso();
  const maxAttempts = config.notificationMaxAttempts;

  const insertEventStmt = ctx.env.DB.prepare(
    `INSERT INTO throne_events (
       id, creator_id, raw_type, normalized_type, event_id, order_id, fallback_hash,
       amount_minor, currency, sender_username, sender_display_name, item_name, item_image_url,
       is_private, is_anonymous, purchased_at, received_at
     )
     SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
      WHERE EXISTS (
        SELECT 1 FROM throne_creators WHERE id = ? AND route_secret_hash = ?
      )`,
  ).bind(
    eventRowId,
    creatorId,
    parsed.rawType,
    parsed.normalizedType,
    parsed.eventId,
    parsed.orderId,
    parsed.fallbackHash,
    parsed.amountMinor,
    parsed.currency,
    parsed.senderUsername,
    parsed.senderDisplayName,
    parsed.itemName,
    parsed.itemImageUrl,
    parsed.isPrivate ? 1 : 0,
    parsed.isAnonymous ? 1 : 0,
    parsed.purchasedAt,
    receivedAt,
    creatorId,
    presentedHash,
  );

  const markVerifiedStmt = webhookVerificationStatement(
    ctx.env,
    creatorId,
    presentedHash,
    receivedAt,
  );

  // Attribution only ever runs when the parser has a sender name to match at all; it has already
  // nulled both sender fields for private/anonymous events, so those never reach this branch.
  const senderIdByGuild = new Map<string, string | null>();
  const fanOutStatements: D1PreparedStatement[] = [];
  for (const registration of activeRegistrations) {
    let senderDiscordUserId: string | null = null;
    if (parsed.senderUsername !== null || parsed.senderDisplayName !== null) {
      if (!senderIdByGuild.has(registration.guild_id)) {
        senderIdByGuild.set(
          registration.guild_id,
          await resolveSenderDiscordUserId(ctx.env, registration.guild_id, parsed.senderUsername, parsed.senderDisplayName),
        );
      }
      senderDiscordUserId = senderIdByGuild.get(registration.guild_id) ?? null;
    }

    const sendId = newId();
    const notificationId = newId();
    fanOutStatements.push(
      ctx.env.DB.prepare(
        `INSERT INTO sends (id, event_id, guild_id, registration_id, sender_discord_user_id, created_at)
         SELECT ?, ?, ?, ?, ?, ?
          WHERE EXISTS (SELECT 1 FROM throne_events WHERE id = ?)`,
      ).bind(sendId, eventRowId, registration.guild_id, registration.id, senderDiscordUserId, receivedAt, eventRowId),
      ctx.env.DB.prepare(
        `INSERT INTO notifications (id, send_id, status, attempts, max_attempts, next_attempt_at, created_at, updated_at)
         SELECT ?, ?, 'pending', 0, ?, ?, ?, ?
          WHERE EXISTS (SELECT 1 FROM sends WHERE id = ?)`,
      ).bind(notificationId, sendId, maxAttempts, receivedAt, receivedAt, receivedAt, sendId),
    );
  }

  try {
    const results = await ctx.env.DB.batch([
      insertEventStmt,
      markVerifiedStmt,
      ...fanOutStatements,
    ]);
    if (results[1]?.meta.changes !== 1) {
      return Errors.notFound("Route not found");
    }
  } catch (error) {
    if (!isUniqueConstraintError(error)) {
      console.error("Failed to record Throne event", error instanceof Error ? error.message : "unknown");
      return Errors.internal();
    }
    // A concurrent or retried delivery already recorded this event; the
    // batch above rolled back entirely, so no partial fan-out occurred. A
    // duplicate of a real, supported event still proves the webhook works,
    // so it marks verification on its own.
    const verification = await webhookVerificationStatement(
      ctx.env,
      creatorId,
      presentedHash,
      nowIso(),
    ).run();
    if (verification.meta.changes !== 1) {
      return Errors.notFound("Route not found");
    }
    return ok({ status: "duplicate" });
  }

  return ok({ status: "recorded", event_id: eventRowId, sent_to_guilds: activeRegistrations.length });
}
