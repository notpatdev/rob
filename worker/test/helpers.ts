import { createExecutionContext, env, waitOnExecutionContext } from "cloudflare:test";
import worker from "../src/index";
import { sha256Hex } from "../src/util/hash";

export const TEST_BOT_TOKEN = "test-bot-token";

export function authHeaders(extra?: Record<string, string>): Record<string, string> {
  return { Authorization: `Bearer ${TEST_BOT_TOKEN}`, ...extra };
}
export async function callWorker(request: Request): Promise<Response> {
  const ctx = createExecutionContext();
  const response = await worker.fetch(request, env, ctx);
  await waitOnExecutionContext(ctx);
  return response;
}

export function jsonRequest(
  method: string,
  path: string,
  body?: unknown,
  headers?: Record<string, string>,
): Request {
  const init: RequestInit = {
    method,
    headers: { "content-type": "application/json", ...headers },
  };
  if (body !== undefined) init.body = JSON.stringify(body);
  return new Request(`https://worker.test${path}`, init);
}

export async function readJson<T = unknown>(response: Response): Promise<T> {
  return (await response.json()) as T;
}

/** Generates an Ed25519 keypair usable both for signing test payloads and as env.THRONE_PUBLIC_KEY_PEM. */
export async function generateThroneKeyPair(): Promise<{
  publicKeyPem: string;
  sign: (timestamp: string, rawBody: string) => Promise<string>;
}> {
  const keyPair = (await crypto.subtle.generateKey({ name: "Ed25519" }, true, [
    "sign",
    "verify",
  ])) as CryptoKeyPair;

  const spki = (await crypto.subtle.exportKey("spki", keyPair.publicKey)) as ArrayBuffer;
  const publicKeyPem = toPem(new Uint8Array(spki));

  const sign = async (timestamp: string, rawBody: string): Promise<string> => {
    const message = new TextEncoder().encode(`${timestamp}.${rawBody}`);
    const signature = await crypto.subtle.sign({ name: "Ed25519" }, keyPair.privateKey, message);
    return bytesToHex(new Uint8Array(signature));
  };

  return { publicKeyPem, sign };
}

function toPem(bytes: Uint8Array): string {
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  const base64 = btoa(binary);
  const lines = base64.match(/.{1,64}/g) ?? [base64];
  return `-----BEGIN PUBLIC KEY-----\n${lines.join("\n")}\n-----END PUBLIC KEY-----`;
}

function bytesToHex(bytes: Uint8Array): string {
  let out = "";
  for (const byte of bytes) out += byte.toString(16).padStart(2, "0");
  return out;
}

export async function seedGuild(guildId: string, channelId = "1000000000000000001"): Promise<void> {
  const now = new Date().toISOString();
  await env.DB.prepare(
    "INSERT INTO guilds (guild_id, send_channel_id, created_at, updated_at) VALUES (?, ?, ?, ?)",
  )
    .bind(guildId, channelId, now, now)
    .run();
}

export async function seedCreator(options: {
  id: string;
  publicCreatorId?: string;
  handle?: string;
  ownerDiscordUserId?: string;
  secret?: string;
}): Promise<{ id: string; secret: string }> {
  const secret = options.secret ?? crypto.randomUUID();
  const secretHash = await sha256Hex(secret);
  const now = new Date().toISOString();
  await env.DB.prepare(
    `INSERT INTO throne_creators
       (id, public_creator_id, handle, profile_url, route_secret_hash, owner_discord_user_id, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
  )
    .bind(
      options.id,
      options.publicCreatorId ?? `public-${options.id}`,
      options.handle ?? "creator",
      `https://throne.com/${options.handle ?? "creator"}`,
      secretHash,
      options.ownerDiscordUserId ?? "1",
      now,
      now,
    )
    .run();
  return { id: options.id, secret };
}

export interface SeedNotificationOptions {
  guildId: string;
  creatorId: string;
  registrationId?: string;
  eventId?: string;
  recipientUserId?: string;
  channelId?: string;
  senderUsername?: string | null;
  amountMinor?: number;
  currency?: string;
  itemName?: string | null;
  isPrivate?: boolean;
  isAnonymous?: boolean;
  purchasedAt?: string;
  status?: "pending" | "leased" | "acked" | "dead_letter";
  attempts?: number;
  maxAttempts?: number;
  leaseToken?: string | null;
  leaseOwner?: string | null;
  leaseExpiresAt?: string | null;
  nextAttemptAt?: string;
}

/** Seeds a full guild/creator/registration/event/send/notification chain directly, bypassing the
 * webhook route, so lease/ack/nack tests can control every field precisely. Assumes the guild and
 * creator (and, unless registrationId is given, a registration linking them) already exist or are
 * created alongside via the options below. */
export async function seedNotificationChain(
  options: SeedNotificationOptions,
): Promise<{ eventDbId: string; sendId: string; notificationId: string; registrationId: string }> {
  const now = new Date().toISOString();
  const eventDbId = crypto.randomUUID();
  const sendId = crypto.randomUUID();
  const notificationId = crypto.randomUUID();
  const eventId = options.eventId ?? crypto.randomUUID();
  const recipientUserId = options.recipientUserId ?? "1";

  let registrationId = options.registrationId;
  if (!registrationId) {
    registrationId = crypto.randomUUID();
    await seedRegistration({
      id: registrationId,
      guildId: options.guildId,
      creatorId: options.creatorId,
      discordUserId: recipientUserId,
    });
  }

  const senderUsername = options.isPrivate || options.isAnonymous ? null : (options.senderUsername ?? "supporter");

  await env.DB.prepare(
    `INSERT INTO throne_events
       (id, creator_id, raw_type, normalized_type, event_id, order_id, fallback_hash,
        amount_minor, currency, sender_username, sender_display_name, item_name, item_image_url,
        is_private, is_anonymous, purchased_at, received_at)
     VALUES (?, ?, 'gift_purchased', 'gift_purchased', ?, NULL, NULL, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?)`,
  )
    .bind(
      eventDbId,
      options.creatorId,
      eventId,
      options.isPrivate ? 0 : (options.amountMinor ?? 1000),
      options.currency ?? "USD",
      senderUsername,
      senderUsername,
      options.itemName ?? "Coffee",
      options.isPrivate ? 1 : 0,
      options.isAnonymous ? 1 : 0,
      options.purchasedAt ?? now,
      now,
    )
    .run();

  await env.DB.prepare(
    `INSERT INTO sends (id, event_id, guild_id, registration_id, created_at) VALUES (?, ?, ?, ?, ?)`,
  )
    .bind(sendId, eventDbId, options.guildId, registrationId, now)
    .run();

  await env.DB.prepare(
    `INSERT INTO notifications
       (id, send_id, status, attempts, max_attempts, next_attempt_at,
        lease_token, lease_owner, lease_expires_at, message_id, last_error, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)`,
  )
    .bind(
      notificationId,
      sendId,
      options.status ?? "pending",
      options.attempts ?? 0,
      options.maxAttempts ?? 5,
      options.nextAttemptAt ?? now,
      options.leaseToken ?? null,
      options.leaseOwner ?? null,
      options.leaseExpiresAt ?? null,
      now,
      now,
    )
    .run();

  return { eventDbId, sendId, notificationId, registrationId };
}

export async function seedRegistration(options: {
  id: string;
  guildId: string;
  creatorId: string;
  discordUserId?: string;
  active?: boolean;
}): Promise<void> {
  const now = new Date().toISOString();
  await env.DB.prepare(
    `INSERT INTO domme_registrations (id, guild_id, creator_id, discord_user_id, active, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?)`,
  )
    .bind(
      options.id,
      options.guildId,
      options.creatorId,
      options.discordUserId ?? "1",
      options.active === false ? 0 : 1,
      now,
      now,
    )
    .run();
}
