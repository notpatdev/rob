import type { RouteContext } from "../router.js";
import { resolveConfig } from "../env.js";
import { Errors, ok } from "../util/response.js";
import { isSnowflake } from "../util/snowflake.js";
import { newLeaseToken, nowIso } from "../util/id.js";

const MAX_LEASE_LIMIT = 100;
const MAX_LEASE_SECONDS = 3600;

interface LeaseCandidateRow {
  id: string;
  status: string;
  attempts: number;
}

interface NotificationDetailRow {
  notification_id: string;
  lease_token: string;
  send_id: string;
  guild_id: string;
  channel_id: string;
  recipient_user_id: string;
  throne_handle: string;
  amount_minor: number;
  currency: string;
  sender_name: string | null;
  is_private: number;
  is_anonymous: number;
  item_name: string | null;
  item_image_url: string | null;
  purchased_at: string;
}

function parseBoundedInt(value: unknown, min: number, max: number): number | null {
  if (typeof value !== "number" || !Number.isInteger(value)) return null;
  if (value < min || value > max) return null;
  return value;
}

export async function handleLeaseNotifications(ctx: RouteContext): Promise<Response> {
  let body: unknown;
  try {
    body = await ctx.request.json();
  } catch {
    return Errors.badRequest("Request body must be JSON", "invalid_body");
  }
  if (typeof body !== "object" || body === null) {
    return Errors.badRequest("Request body must be a JSON object", "invalid_body");
  }
  const record = body as Record<string, unknown>;

  const owner = record.owner;
  if (typeof owner !== "string" || owner.trim().length === 0) {
    return Errors.badRequest("owner must be a non-empty string", "invalid_owner");
  }
  const limit = parseBoundedInt(record.limit, 1, MAX_LEASE_LIMIT);
  if (limit === null) {
    return Errors.badRequest(`limit must be an integer between 1 and ${MAX_LEASE_LIMIT}`, "invalid_limit");
  }
  const leaseSeconds = parseBoundedInt(record.lease_seconds, 1, MAX_LEASE_SECONDS);
  if (leaseSeconds === null) {
    return Errors.badRequest(
      `lease_seconds must be an integer between 1 and ${MAX_LEASE_SECONDS}`,
      "invalid_lease_seconds",
    );
  }

  const now = nowIso();
  const leaseExpiresAt = new Date(Date.now() + leaseSeconds * 1000).toISOString();

  // Overfetch candidates modestly so lost claim races (another instance
  // grabbing a row first) don't starve this call of a full batch.
  const candidateLimit = Math.min(limit * 3, MAX_LEASE_LIMIT * 3);
  const candidates = await ctx.env.DB.prepare(
    `SELECT id, status, attempts FROM notifications
     WHERE (status = 'pending' AND next_attempt_at <= ?)
        OR (status = 'leased' AND lease_expires_at < ?)
     ORDER BY next_attempt_at ASC
     LIMIT ?`,
  )
    .bind(now, now, candidateLimit)
    .all<LeaseCandidateRow>();

  const claimedIds: string[] = [];
  // A prior delivery attempt may already have reached Discord even though
  // this row is being (re-)claimed: either the row was reclaimed from an
  // expired `leased` state (the owner could have crashed after posting but
  // before acking), or it already carries attempts from an earlier nack.
  // Ordinary first-attempt `pending` rows with zero attempts can never have
  // been posted, so callers can skip the expensive duplicate-message scan
  // for those and only pay for it when recovery is actually possible.
  const deliveryMayExistById = new Map<string, boolean>();
  for (const candidate of candidates.results ?? []) {
    if (claimedIds.length >= limit) break;
    const leaseToken = newLeaseToken();
    const result = await ctx.env.DB.prepare(
      `UPDATE notifications
       SET status = 'leased', lease_token = ?, lease_owner = ?, lease_expires_at = ?, updated_at = ?
       WHERE id = ?
         AND ((status = 'pending' AND next_attempt_at <= ?) OR (status = 'leased' AND lease_expires_at < ?))`,
    )
      .bind(leaseToken, owner, leaseExpiresAt, now, candidate.id, now, now)
      .run();
    if (result.meta.changes === 1) {
      claimedIds.push(candidate.id);
      deliveryMayExistById.set(candidate.id, candidate.status === "leased" || candidate.attempts > 0);
    }
  }

  if (claimedIds.length === 0) return ok({ notifications: [] });

  const placeholders = claimedIds.map(() => "?").join(", ");
  const details = await ctx.env.DB.prepare(
    `SELECT
       n.id AS notification_id,
       n.lease_token AS lease_token,
       n.send_id AS send_id,
       s.guild_id AS guild_id,
       g.send_channel_id AS channel_id,
       r.discord_user_id AS recipient_user_id,
       c.handle AS throne_handle,
       e.amount_minor AS amount_minor,
       e.currency AS currency,
       COALESCE(e.sender_display_name, e.sender_username) AS sender_name,
       e.is_private AS is_private,
       e.is_anonymous AS is_anonymous,
       e.item_name AS item_name,
       e.item_image_url AS item_image_url,
       COALESCE(e.purchased_at, e.received_at) AS purchased_at
     FROM notifications n
     JOIN sends s ON s.id = n.send_id
     JOIN guilds g ON g.guild_id = s.guild_id
     JOIN domme_registrations r ON r.id = s.registration_id
     JOIN throne_events e ON e.id = s.event_id
     JOIN throne_creators c ON c.id = e.creator_id
     WHERE n.id IN (${placeholders})`,
  )
    .bind(...claimedIds)
    .all<NotificationDetailRow>();

  const detailRows: NotificationDetailRow[] = details.results ?? [];
  const byId = new Map(detailRows.map((row) => [row.notification_id, row]));
  const notifications = claimedIds
    .map((id) => byId.get(id))
    .filter((row): row is NotificationDetailRow => row !== undefined)
    .map((row) => ({
      notification_id: row.notification_id,
      lease_token: row.lease_token,
      send_id: row.send_id,
      guild_id: row.guild_id,
      channel_id: row.channel_id,
      recipient_user_id: row.recipient_user_id,
      throne_handle: row.throne_handle,
      amount_minor: row.amount_minor,
      currency: row.currency,
      sender_name: row.sender_name,
      is_private: Boolean(row.is_private),
      is_anonymous: Boolean(row.is_anonymous),
      item_name: row.item_name,
      item_image_url: row.item_image_url,
      purchased_at: row.purchased_at,
      delivery_may_exist: deliveryMayExistById.get(row.notification_id) ?? true,
    }));

  return ok({ notifications });
}

interface LeaseOwnershipRow {
  status: string;
  lease_token: string | null;
  lease_expires_at: string | null;
  attempts: number;
  max_attempts: number;
}

async function loadLeasedNotification(
  ctx: RouteContext,
  notificationId: string,
  leaseToken: string,
): Promise<LeaseOwnershipRow | null> {
  const row = await ctx.env.DB.prepare(
    "SELECT status, lease_token, lease_expires_at, attempts, max_attempts FROM notifications WHERE id = ?",
  )
    .bind(notificationId)
    .first<LeaseOwnershipRow>();
  if (!row) return null;
  const now = nowIso();
  const leaseValid =
    row.status === "leased" &&
    row.lease_token === leaseToken &&
    row.lease_expires_at !== null &&
    row.lease_expires_at > now;
  return leaseValid ? row : null;
}

export async function handleAckNotification(ctx: RouteContext): Promise<Response> {
  const notificationId = ctx.params.id ?? "";
  if (!notificationId) return Errors.badRequest("notification id is required", "invalid_notification_id");

  let body: unknown;
  try {
    body = await ctx.request.json();
  } catch {
    return Errors.badRequest("Request body must be JSON", "invalid_body");
  }
  if (typeof body !== "object" || body === null) {
    return Errors.badRequest("Request body must be a JSON object", "invalid_body");
  }
  const record = body as Record<string, unknown>;

  const leaseToken = record.lease_token;
  if (typeof leaseToken !== "string" || leaseToken.length === 0) {
    return Errors.badRequest("lease_token must be a non-empty string", "invalid_lease_token");
  }
  const discordMessageId = record.discord_message_id;
  if (!isSnowflake(discordMessageId)) {
    return Errors.badRequest("discord_message_id must be a Discord snowflake", "invalid_message_id");
  }

  const leased = await loadLeasedNotification(ctx, notificationId, leaseToken);
  if (!leased) {
    const exists = await ctx.env.DB.prepare("SELECT status FROM notifications WHERE id = ?")
      .bind(notificationId)
      .first();
    if (!exists) return Errors.notFound("Notification not found");
    return Errors.conflict("Lease is no longer valid", "lease_invalid");
  }

  const now = nowIso();
  const result = await ctx.env.DB.prepare(
    `UPDATE notifications
     SET status = 'acked', message_id = ?, lease_token = NULL, lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
     WHERE id = ? AND lease_token = ? AND status = 'leased'`,
  )
    .bind(discordMessageId, now, notificationId, leaseToken)
    .run();
  if (result.meta.changes !== 1) return Errors.conflict("Lease is no longer valid", "lease_invalid");

  return ok({ notification_id: notificationId, status: "acked" });
}

export async function handleNackNotification(ctx: RouteContext): Promise<Response> {
  const notificationId = ctx.params.id ?? "";
  if (!notificationId) return Errors.badRequest("notification id is required", "invalid_notification_id");

  let body: unknown;
  try {
    body = await ctx.request.json();
  } catch {
    return Errors.badRequest("Request body must be JSON", "invalid_body");
  }
  if (typeof body !== "object" || body === null) {
    return Errors.badRequest("Request body must be a JSON object", "invalid_body");
  }
  const record = body as Record<string, unknown>;

  const leaseToken = record.lease_token;
  if (typeof leaseToken !== "string" || leaseToken.length === 0) {
    return Errors.badRequest("lease_token must be a non-empty string", "invalid_lease_token");
  }
  const errorMessage = typeof record.error === "string" ? record.error.slice(0, 300) : "unknown error";
  const permanent = record.permanent === true;

  const leased = await loadLeasedNotification(ctx, notificationId, leaseToken);
  if (!leased) {
    const exists = await ctx.env.DB.prepare("SELECT status FROM notifications WHERE id = ?")
      .bind(notificationId)
      .first();
    if (!exists) return Errors.notFound("Notification not found");
    return Errors.conflict("Lease is no longer valid", "lease_invalid");
  }

  const config = resolveConfig(ctx.env);
  const attempts = leased.attempts + 1;
  const now = nowIso();
  const deadLetter = permanent || attempts >= leased.max_attempts;

  let result;
  if (deadLetter) {
    result = await ctx.env.DB.prepare(
      `UPDATE notifications
       SET status = 'dead_letter', attempts = ?, last_error = ?,
           lease_token = NULL, lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
       WHERE id = ? AND lease_token = ? AND status = 'leased'`,
    )
      .bind(attempts, errorMessage, now, notificationId, leaseToken)
      .run();
  } else {
    const backoffSeconds = Math.min(
      config.notificationBackoffBaseSeconds * 2 ** (attempts - 1),
      config.notificationBackoffMaxSeconds,
    );
    const nextAttemptAt = new Date(Date.now() + backoffSeconds * 1000).toISOString();
    result = await ctx.env.DB.prepare(
      `UPDATE notifications
       SET status = 'pending', attempts = ?, last_error = ?, next_attempt_at = ?,
           lease_token = NULL, lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
       WHERE id = ? AND lease_token = ? AND status = 'leased'`,
    )
      .bind(attempts, errorMessage, nextAttemptAt, now, notificationId, leaseToken)
      .run();
  }
  if (result.meta.changes !== 1) return Errors.conflict("Lease is no longer valid", "lease_invalid");

  return ok({ notification_id: notificationId, status: deadLetter ? "dead_letter" : "pending", attempts });
}
