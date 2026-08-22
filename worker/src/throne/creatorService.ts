/**
 * Shared Throne creator resolution/registration logic: resolving a
 * caller-provided username/URL to a Throne creator, creating or reusing the
 * `throne_creators` row for it, and issuing/rotating its webhook secret.
 * This is the one place that logic lives -- both the legacy guild-scoped
 * `/register` route and the profile draft's Throne step call into it, so
 * "issue a webhook secret exactly once, store only its hash, support
 * explicit rotation" is guaranteed to behave identically everywhere a
 * Dom/me connects Throne.
 */
import type { Env } from "../env.js";
import { newId, newRouteSecret, nowIso } from "../util/id.js";
import { sha256Hex } from "../util/hash.js";
import { normalizeThroneInput } from "./normalize.js";
import { resolveThroneCreator } from "./resolve.js";

export class ThroneResolutionError extends Error {
  readonly code: string;
  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

interface CreatorRow {
  id: string;
  public_creator_id: string;
  handle: string;
  owner_discord_user_id: string;
}

export function buildWebhookUrl(publicBaseUrl: string, creatorId: string, secret: string): string {
  return `${publicBaseUrl.replace(/\/+$/, "")}/t/${creatorId}/${secret}`;
}

export type WebhookState = "issued" | "existing" | "rotated";

export interface ThroneAttachResult {
  readonly creatorId: string;
  readonly handle: string;
  /** Only non-null when a secret was just issued or rotated -- the one moment its plaintext
   * value is ever available; every other read of `throne_creators` only has the hash. */
  readonly webhookUrl: string | null;
  readonly webhookState: WebhookState;
}

/**
 * Resolves `rawThroneInput` (a Throne username or profile URL) to a public
 * Throne creator, then creates a brand-new `throne_creators` row for it (if
 * one does not already exist) or reuses/rotates the existing one already
 * owned by `ownerUserId`. Throws `ThroneResolutionError` (never a bare
 * `Error`) for every caller-facing failure so route handlers can map its
 * `code` to the right HTTP status.
 */
export async function resolveOrAttachThroneCreator(
  env: Env,
  ownerUserId: string,
  rawThroneInput: string,
  options: { rotateWebhook: boolean },
): Promise<ThroneAttachResult> {
  const normalized = normalizeThroneInput(rawThroneInput);
  if (!normalized) {
    throw new ThroneResolutionError("invalid_throne_input", "throne must be a Throne username or profile URL");
  }

  const resolved = await resolveThroneCreator(normalized.username);
  if (!resolved) {
    throw new ThroneResolutionError("throne_creator_not_found", "Could not resolve that Throne creator");
  }

  const now = nowIso();
  const existing = await env.DB.prepare(
    "SELECT id, public_creator_id, handle, owner_discord_user_id FROM throne_creators WHERE public_creator_id = ?",
  )
    .bind(resolved.publicCreatorId)
    .first<CreatorRow>();

  if (existing && existing.owner_discord_user_id !== ownerUserId) {
    throw new ThroneResolutionError(
      "creator_owned",
      "That Throne creator is already linked by a different Discord user",
    );
  }

  let creatorId: string;
  let webhookUrl: string | null = null;
  let webhookState: WebhookState;

  if (!existing) {
    creatorId = newId();
    const secret = newRouteSecret();
    const secretHash = await sha256Hex(secret);
    await env.DB.prepare(
      `INSERT INTO throne_creators
         (id, public_creator_id, handle, profile_url, route_secret_hash, owner_discord_user_id, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
    )
      .bind(creatorId, resolved.publicCreatorId, resolved.handle, normalized.profileUrl, secretHash, ownerUserId, now, now)
      .run();
    webhookUrl = buildWebhookUrl(env.PUBLIC_BASE_URL, creatorId, secret);
    webhookState = "issued";
  } else {
    creatorId = existing.id;
    // Keep the cached handle/profile URL fresh; this never touches the secret.
    await env.DB.prepare("UPDATE throne_creators SET handle = ?, profile_url = ?, updated_at = ? WHERE id = ?")
      .bind(resolved.handle, normalized.profileUrl, now, creatorId)
      .run();

    if (options.rotateWebhook) {
      const rotated = await rotateThroneWebhookSecret(env, creatorId);
      webhookUrl = rotated.webhookUrl;
      webhookState = "rotated";
    } else {
      webhookState = "existing";
    }
  }

  return { creatorId, handle: resolved.handle, webhookUrl, webhookState };
}

/** Rotates an existing creator's webhook secret unconditionally, returning the new (one-time
 * visible) webhook URL. Ownership must already have been checked by the caller. */
export async function rotateThroneWebhookSecret(env: Env, creatorId: string): Promise<{ webhookUrl: string }> {
  const secret = newRouteSecret();
  const secretHash = await sha256Hex(secret);
  const now = nowIso();
  await env.DB.prepare("UPDATE throne_creators SET route_secret_hash = ?, updated_at = ? WHERE id = ?")
    .bind(secretHash, now, creatorId)
    .run();
  return { webhookUrl: buildWebhookUrl(env.PUBLIC_BASE_URL, creatorId, secret) };
}

/** Maps a `ThroneResolutionError` code to the HTTP status the legacy `/register` route and the
 * profile draft Throne routes both use -- kept in one place so the two surfaces agree. */
export function httpStatusForThroneErrorCode(code: string): number {
  if (code === "throne_creator_not_found") return 404;
  if (code === "creator_owned") return 409;
  return 400;
}
