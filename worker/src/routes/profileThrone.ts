import type { RouteContext } from "../router.js";
import { Errors, fail, ok } from "../util/response.js";
import { isSnowflake } from "../util/snowflake.js";
import { HomeGuildNotConfiguredError } from "../env.js";
import { DraftError } from "../profile/draftService.js";
import { serializeDraftContract } from "./profileDrafts.js";
import {
  attachThroneToDraft,
  getDraftThroneStatus,
  resolveThroneForDraft,
  rotateDraftThroneWebhook,
  type ThroneDraftResult,
  type ThroneResolveResult,
} from "../profile/throneDraftService.js";

function serializeThroneResult(result: ThroneDraftResult) {
  return {
    draft: serializeDraftContract(result.draft),
    webhook_url: result.webhookUrl,
    webhook_state: result.webhookState,
  };
}

async function readJsonBody(request: Request): Promise<Record<string, unknown> | null> {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return null;
  }
  if (typeof body !== "object" || body === null || Array.isArray(body)) return null;
  return body as Record<string, unknown>;
}

async function runThroneOperation<T>(
  operation: () => Promise<T>,
): Promise<{ ok: true; value: T } | { ok: false; response: Response }> {
  try {
    return { ok: true, value: await operation() };
  } catch (error) {
    if (error instanceof DraftError) return { ok: false, response: fail(error.status, error.code, error.message) };
    if (error instanceof HomeGuildNotConfiguredError) {
      return { ok: false, response: Errors.internal("Worker is not configured with a valid BILL_HOME_GUILD_ID") };
    }
    throw error;
  }
}

function parseCommonMutationFields(body: Record<string, unknown> | null): { ownerUserId: string; expectedRevision: number } | Response {
  if (body === null) return Errors.badRequest("Request body must be a JSON object", "invalid_body");
  const ownerUserId = body.owner_user_id;
  if (!isSnowflake(ownerUserId)) return Errors.badRequest("owner_user_id must be a Discord snowflake", "invalid_owner_user_id");
  const expectedRevision = body.expected_revision;
  if (typeof expectedRevision !== "number" || !Number.isInteger(expectedRevision) || expectedRevision < 0) {
    return Errors.badRequest("expected_revision must be a non-negative integer", "invalid_expected_revision");
  }
  return { ownerUserId, expectedRevision };
}

function serializeResolveResult(result: ThroneResolveResult) {
  return {
    draft: serializeDraftContract(result.draft),
    handle: result.handle,
    already_verified: result.alreadyVerified,
    confirmation_token: result.confirmationToken,
    expires_at: result.expiresAt,
  };
}

/**
 * `POST /v1/profile-drafts/:draftId/throne/resolve` -- step one of connecting
 * Throne: look up the submitted username/profile URL and stage what was found
 * for confirmation. Creates no creator row, mints no webhook secret, and
 * returns no identifiers -- only the handle to confirm, whether that creator is
 * already verified for this same user, and a one-time confirmation capability
 * to send back to `POST .../throne`.
 */
export async function handleResolveDraftThrone(ctx: RouteContext): Promise<Response> {
  const draftId = ctx.params.draftId ?? "";
  const body = await readJsonBody(ctx.request);
  const common = parseCommonMutationFields(body);
  if (common instanceof Response) return common;

  const throneInputRaw = body!.throne_input;
  if (typeof throneInputRaw !== "string" || throneInputRaw.trim().length === 0) {
    return Errors.badRequest("throne_input must be a non-empty string", "invalid_throne_input");
  }

  const result = await runThroneOperation(() =>
    resolveThroneForDraft(ctx.env, {
      draftId,
      ownerUserId: common.ownerUserId,
      expectedRevision: common.expectedRevision,
      throneInput: throneInputRaw,
    }),
  );
  if (!result.ok) return result.response;
  return ok(serializeResolveResult(result.value));
}

/** `POST /v1/profile-drafts/:draftId/throne` -- attach a Throne creator to the draft's document,
 * confirming a staged resolution (`confirmation_token`), reattaching an already-owned creator
 * (`existing_creator_id`), or resolving a username/URL directly (`throne_input`). */
export async function handleAttachDraftThrone(ctx: RouteContext): Promise<Response> {
  const draftId = ctx.params.draftId ?? "";
  const body = await readJsonBody(ctx.request);
  const common = parseCommonMutationFields(body);
  if (common instanceof Response) return common;

  const throneInputRaw = body!.throne_input;
  const existingCreatorIdRaw = body!.existing_creator_id;
  const confirmationTokenRaw = body!.confirmation_token;
  const confirmPendingRaw = body!.confirm_pending;
  if (throneInputRaw !== undefined && throneInputRaw !== null && typeof throneInputRaw !== "string") {
    return Errors.badRequest("throne_input must be a string or null", "invalid_field");
  }
  if (existingCreatorIdRaw !== undefined && existingCreatorIdRaw !== null && typeof existingCreatorIdRaw !== "string") {
    return Errors.badRequest("existing_creator_id must be a string or null", "invalid_field");
  }
  if (
    confirmationTokenRaw !== undefined &&
    confirmationTokenRaw !== null &&
    (typeof confirmationTokenRaw !== "string" || confirmationTokenRaw.length === 0)
  ) {
    return Errors.badRequest("confirmation_token must be a non-empty string or null", "invalid_field");
  }
  if (confirmPendingRaw !== undefined && typeof confirmPendingRaw !== "boolean") {
    return Errors.badRequest("confirm_pending must be a boolean", "invalid_field");
  }
  const rotateWebhook = body!.rotate_webhook === true;

  const result = await runThroneOperation(() =>
    attachThroneToDraft(ctx.env, {
      draftId,
      ownerUserId: common.ownerUserId,
      expectedRevision: common.expectedRevision,
      throneInput: (throneInputRaw as string | undefined) ?? null,
      existingCreatorId: (existingCreatorIdRaw as string | undefined) ?? null,
      confirmationToken: (confirmationTokenRaw as string | undefined) ?? null,
      confirmPending: confirmPendingRaw === true,
      rotateWebhook,
    }),
  );
  if (!result.ok) return result.response;
  return ok(serializeThroneResult(result.value));
}

/** `POST /v1/profile-drafts/:draftId/throne/rotate` -- explicit secret rotation for the Throne
 * creator already connected to this draft. */
export async function handleRotateDraftThrone(ctx: RouteContext): Promise<Response> {
  const draftId = ctx.params.draftId ?? "";
  const body = await readJsonBody(ctx.request);
  const common = parseCommonMutationFields(body);
  if (common instanceof Response) return common;

  const result = await runThroneOperation(() =>
    rotateDraftThroneWebhook(ctx.env, {
      draftId,
      ownerUserId: common.ownerUserId,
      expectedRevision: common.expectedRevision,
    }),
  );
  if (!result.ok) return result.response;
  return ok(serializeThroneResult(result.value));
}

/**
 * `GET /v1/profile-drafts/:draftId/throne/status?owner_user_id=...&expected_revision=...`
 * -- has Throne actually delivered a signed webhook for this draft's
 * connection yet? Bearer-protected like every other bot route, owner-checked,
 * and pinned to an exact draft revision. The response body carries only
 * `handle`, `verified`, and `verified_at`: never the creator id, the route
 * secret or its hash, or the webhook URL.
 */
export async function handleGetDraftThroneStatus(ctx: RouteContext): Promise<Response> {
  const draftId = ctx.params.draftId ?? "";
  const params = new URL(ctx.request.url).searchParams;
  const ownerUserId = params.get("owner_user_id");
  if (!isSnowflake(ownerUserId)) {
    return Errors.badRequest("owner_user_id must be a Discord snowflake", "invalid_owner_user_id");
  }
  const rawRevision = params.get("expected_revision");
  const expectedRevision = rawRevision === null ? Number.NaN : Number(rawRevision);
  if (!/^\d+$/.test(rawRevision ?? "") || !Number.isSafeInteger(expectedRevision)) {
    return Errors.badRequest("expected_revision must be a non-negative integer", "invalid_expected_revision");
  }

  const result = await runThroneOperation(() =>
    getDraftThroneStatus(ctx.env, { draftId, ownerUserId, expectedRevision }),
  );
  if (!result.ok) return result.response;
  return ok({
    handle: result.value.handle,
    verified: result.value.verified,
    verified_at: result.value.verifiedAt,
  });
}
