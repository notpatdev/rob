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

export interface PreparedThroneAttachment extends ThroneAttachResult {
  readonly publicCreatorId: string;
  readonly profileUrl: string;
  readonly ownerUserId: string;
  readonly existing: boolean;
  readonly routeSecretHash: string | null;
}

export interface PreparedWebhookSecret {
  readonly creatorId: string;
  readonly routeSecretHash: string;
  readonly webhookUrl: string;
}

export interface SqlMutationGuard {
  readonly sql: string;
  readonly params: readonly unknown[];
}

/**
 * Performs network resolution and prepares secret material without writing D1.
 * Profile drafts use the returned values to put the creator mutation and their
 * revision CAS in one guarded batch; the legacy API executes the same prepared
 * statement immediately.
 */
export async function prepareThroneCreatorAttachment(
  env: Env,
  ownerUserId: string,
  rawThroneInput: string,
  options: { rotateWebhook: boolean },
): Promise<PreparedThroneAttachment> {
  const normalized = normalizeThroneInput(rawThroneInput);
  if (!normalized) {
    throw new ThroneResolutionError("invalid_throne_input", "throne must be a Throne username or profile URL");
  }

  const resolved = await resolveThroneCreator(normalized.username);
  if (!resolved) {
    throw new ThroneResolutionError("throne_creator_not_found", "Could not resolve that Throne creator");
  }

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
  let routeSecretHash: string | null = null;

  if (!existing) {
    creatorId = newId();
    const secret = newRouteSecret();
    routeSecretHash = await sha256Hex(secret);
    webhookUrl = buildWebhookUrl(env.PUBLIC_BASE_URL, creatorId, secret);
    webhookState = "issued";
  } else {
    creatorId = existing.id;
    if (options.rotateWebhook) {
      const rotated = await prepareWebhookSecret(env, creatorId);
      webhookUrl = rotated.webhookUrl;
      routeSecretHash = rotated.routeSecretHash;
      webhookState = "rotated";
    } else {
      webhookState = "existing";
    }
  }

  return {
    creatorId,
    handle: resolved.handle,
    webhookUrl,
    webhookState,
    publicCreatorId: resolved.publicCreatorId,
    profileUrl: normalized.profileUrl,
    ownerUserId,
    existing: existing !== null,
    routeSecretHash,
  };
}

export function buildPreparedCreatorStatements(
  env: Env,
  prepared: PreparedThroneAttachment,
  guard: SqlMutationGuard | null = null,
): D1PreparedStatement[] {
  const now = nowIso();
  if (!prepared.existing) {
    const where = guard === null ? "" : ` WHERE ${guard.sql}`;
    return [
      env.DB.prepare(
        `INSERT INTO throne_creators
           (id, public_creator_id, handle, profile_url, route_secret_hash,
            owner_discord_user_id, created_at, updated_at)
         SELECT ?, ?, ?, ?, ?, ?, ?, ?${where}`,
      ).bind(
        prepared.creatorId,
        prepared.publicCreatorId,
        prepared.handle,
        prepared.profileUrl,
        prepared.routeSecretHash,
        prepared.ownerUserId,
        now,
        now,
        ...(guard?.params ?? []),
      ),
    ];
  }

  const guardSuffix = guard === null ? "" : ` AND ${guard.sql}`;
  if (prepared.routeSecretHash !== null) {
    return [
      env.DB.prepare(
        `UPDATE throne_creators
            SET handle = ?, profile_url = ?, route_secret_hash = ?, updated_at = ?
          WHERE id = ? AND owner_discord_user_id = ?${guardSuffix}`,
      ).bind(
        prepared.handle,
        prepared.profileUrl,
        prepared.routeSecretHash,
        now,
        prepared.creatorId,
        prepared.ownerUserId,
        ...(guard?.params ?? []),
      ),
    ];
  }
  return [
    env.DB.prepare(
      `UPDATE throne_creators
          SET handle = ?, profile_url = ?, updated_at = ?
        WHERE id = ? AND owner_discord_user_id = ?${guardSuffix}`,
    ).bind(
      prepared.handle,
      prepared.profileUrl,
      now,
      prepared.creatorId,
      prepared.ownerUserId,
      ...(guard?.params ?? []),
    ),
  ];
}

export async function resolveOrAttachThroneCreator(
  env: Env,
  ownerUserId: string,
  rawThroneInput: string,
  options: { rotateWebhook: boolean },
): Promise<ThroneAttachResult> {
  const prepared = await prepareThroneCreatorAttachment(env, ownerUserId, rawThroneInput, options);
  const results = await env.DB.batch(buildPreparedCreatorStatements(env, prepared));
  if (results[0]?.meta.changes !== 1) {
    throw new ThroneResolutionError("creator_conflict", "The Throne creator changed while it was being linked");
  }
  return {
    creatorId: prepared.creatorId,
    handle: prepared.handle,
    webhookUrl: prepared.webhookUrl,
    webhookState: prepared.webhookState,
  };
}

export async function prepareWebhookSecret(
  env: Env,
  creatorId: string,
): Promise<PreparedWebhookSecret> {
  const secret = newRouteSecret();
  return {
    creatorId,
    routeSecretHash: await sha256Hex(secret),
    webhookUrl: buildWebhookUrl(env.PUBLIC_BASE_URL, creatorId, secret),
  };
}

/** Rotates an existing creator's webhook secret unconditionally, returning the new (one-time
 * visible) webhook URL. Ownership must already have been checked by the caller. */
export async function rotateThroneWebhookSecret(env: Env, creatorId: string): Promise<{ webhookUrl: string }> {
  const prepared = await prepareWebhookSecret(env, creatorId);
  const result = await env.DB.prepare(
    "UPDATE throne_creators SET route_secret_hash = ?, updated_at = ? WHERE id = ?",
  )
    .bind(prepared.routeSecretHash, nowIso(), creatorId)
    .run();
  if (result.meta.changes !== 1) {
    throw new ThroneResolutionError("throne_creator_not_found", "Could not find that Throne creator");
  }
  return { webhookUrl: prepared.webhookUrl };
}

/** Maps a `ThroneResolutionError` code to the HTTP status the legacy `/register` route and the
 * profile draft Throne routes both use -- kept in one place so the two surfaces agree. */
export function httpStatusForThroneErrorCode(code: string): number {
  if (code === "throne_creator_not_found") return 404;
  if (code === "creator_owned") return 409;
  return 400;
}
