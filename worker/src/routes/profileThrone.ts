import type { RouteContext } from "../router.js";
import { Errors, fail, ok } from "../util/response.js";
import { isSnowflake } from "../util/snowflake.js";
import { HomeGuildNotConfiguredError } from "../env.js";
import { DraftError, type DraftContract } from "../profile/draftService.js";
import { attachThroneToDraft, rotateDraftThroneWebhook, type ThroneDraftResult } from "../profile/throneDraftService.js";

function serializeDraftContract(draft: DraftContract) {
  return {
    id: draft.id,
    revision: draft.revision,
    current_step: draft.currentStep,
    next_step: draft.nextStep,
    steps: draft.steps.map((step) => ({ key: step.key, status: step.status, completed_at: step.completedAt })),
    dm_status_selected: draft.dmStatusSelected,
    document: {
      throne_creator_id: draft.document.throneCreatorId,
      preferred_payment_link_id: draft.document.preferredPaymentLinkId,
    },
    throne_prefill:
      draft.thronePrefill === null
        ? null
        : {
            owned_creators: draft.thronePrefill.ownedCreators.map((creator) => ({ id: creator.id, handle: creator.handle })),
            existing_registration_creator_id: draft.thronePrefill.existingRegistrationCreatorId,
          },
    updated_at: draft.updatedAt,
  };
}

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

/** `POST /v1/profile-drafts/:draftId/throne` -- resolve/attach a Throne creator (new username/URL,
 * or an already-owned creator id from this draft's `throne_prefill`) to the draft's document. */
export async function handleAttachDraftThrone(ctx: RouteContext): Promise<Response> {
  const draftId = ctx.params.draftId ?? "";
  const body = await readJsonBody(ctx.request);
  const common = parseCommonMutationFields(body);
  if (common instanceof Response) return common;

  const throneInputRaw = body!.throne_input;
  const existingCreatorIdRaw = body!.existing_creator_id;
  if (throneInputRaw !== undefined && throneInputRaw !== null && typeof throneInputRaw !== "string") {
    return Errors.badRequest("throne_input must be a string or null", "invalid_field");
  }
  if (existingCreatorIdRaw !== undefined && existingCreatorIdRaw !== null && typeof existingCreatorIdRaw !== "string") {
    return Errors.badRequest("existing_creator_id must be a string or null", "invalid_field");
  }
  const rotateWebhook = body!.rotate_webhook === true;

  const result = await runThroneOperation(() =>
    attachThroneToDraft(ctx.env, {
      draftId,
      ownerUserId: common.ownerUserId,
      expectedRevision: common.expectedRevision,
      throneInput: (throneInputRaw as string | undefined) ?? null,
      existingCreatorId: (existingCreatorIdRaw as string | undefined) ?? null,
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
