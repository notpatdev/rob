import type { RouteContext } from "../router.js";
import { Errors, fail, ok } from "../util/response.js";
import { isSnowflake } from "../util/snowflake.js";
import { HomeGuildNotConfiguredError } from "../env.js";
import { DraftError, type DraftContract } from "../profile/draftService.js";
import { removeDraftLink, upsertDraftLink } from "../profile/linkService.js";
import { ValidationError } from "../profile/contracts.js";

/** Mirrors `serializeDraftContract` in `profileDrafts.ts`; kept local so this route module does
 * not need to reach into that one just for a shared response shape. */
function serializeDraftContract(draft: DraftContract) {
  return {
    id: draft.id,
    owner_user_id: draft.ownerUserId,
    origin_guild_id: draft.originGuildId,
    target_scope: draft.targetScope,
    guild_id: draft.guildId,
    server_mode: draft.serverMode,
    status: draft.status,
    revision: draft.revision,
    base_version: draft.baseVersion,
    current_step: draft.currentStep,
    next_step: draft.nextStep,
    steps: draft.steps.map((step) => ({ key: step.key, status: step.status, completed_at: step.completedAt })),
    dm_status_selected: draft.dmStatusSelected,
    governing_orientation: draft.governingOrientation,
    document: {
      dm_status: draft.document.dmStatus,
      bio: draft.document.bio,
      public_send_stats: draft.document.publicSendStats,
      selections: {
        pronouns: draft.document.selections.pronouns,
        honourifics: draft.document.selections.honourifics,
        submissive_labels: draft.document.selections.submissiveLabels,
      },
      aliases: draft.document.aliases,
      links: draft.document.links.map((link) => ({
        id: link.id,
        platform: link.platform,
        public_label: link.publicLabel,
        username: link.username,
        normalized_url: link.normalizedUrl,
        link_type: link.linkType,
        enabled: link.enabled,
      })),
      overridden_fields: draft.document.overriddenFields,
      hidden_inherited_link_ids: draft.document.hiddenInheritedLinkIds,
      throne_creator_id: draft.document.throneCreatorId,
      preferred_payment_link_id: draft.document.preferredPaymentLinkId,
    },
    created_at: draft.createdAt,
    updated_at: draft.updatedAt,
    published_at: draft.publishedAt,
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

async function runLinkOperation<T>(operation: () => Promise<T>): Promise<{ ok: true; value: T } | { ok: false; response: Response }> {
  try {
    return { ok: true, value: await operation() };
  } catch (error) {
    if (error instanceof DraftError) return { ok: false, response: fail(error.status, error.code, error.message) };
    if (error instanceof ValidationError) return { ok: false, response: fail(400, error.code, error.message) };
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

function parseOptionalString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function parseOptionalBoolean(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

/** `POST /v1/profile-drafts/:draftId/links` -- add a new manual link. */
export async function handleAddLink(ctx: RouteContext): Promise<Response> {
  const draftId = ctx.params.draftId ?? "";
  const body = await readJsonBody(ctx.request);
  const common = parseCommonMutationFields(body);
  if (common instanceof Response) return common;

  const publicLabel = body!.public_label;
  const normalizedUrl = body!.normalized_url;
  if (typeof publicLabel !== "string") return Errors.badRequest("public_label must be a string", "invalid_field");
  if (typeof normalizedUrl !== "string") return Errors.badRequest("normalized_url must be a string", "invalid_field");
  const linkType = body!.link_type;
  if (linkType !== undefined && linkType !== null && linkType !== "social" && linkType !== "payment") {
    return Errors.badRequest('link_type must be "social" or "payment"', "invalid_link_type");
  }

  const result = await runLinkOperation(() =>
    upsertDraftLink(ctx.env, {
      draftId,
      ownerUserId: common.ownerUserId,
      expectedRevision: common.expectedRevision,
      linkId: null,
      platform: parseOptionalString(body!.platform),
      publicLabel,
      username: parseOptionalString(body!.username),
      normalizedUrl,
      linkType: (linkType as "social" | "payment" | undefined) ?? null,
      enabled: body!.enabled === undefined ? true : body!.enabled === true,
      preferred: parseOptionalBoolean(body!.preferred),
    }),
  );
  if (!result.ok) return result.response;
  return ok({ draft: serializeDraftContract(result.value) }, 201);
}

/** `PUT /v1/profile-drafts/:draftId/links/:linkId` -- edit an existing manual link. */
export async function handleEditLink(ctx: RouteContext): Promise<Response> {
  const draftId = ctx.params.draftId ?? "";
  const linkId = ctx.params.linkId ?? "";
  const body = await readJsonBody(ctx.request);
  const common = parseCommonMutationFields(body);
  if (common instanceof Response) return common;

  const publicLabel = body!.public_label;
  const normalizedUrl = body!.normalized_url;
  if (typeof publicLabel !== "string") return Errors.badRequest("public_label must be a string", "invalid_field");
  if (typeof normalizedUrl !== "string") return Errors.badRequest("normalized_url must be a string", "invalid_field");
  const linkType = body!.link_type;
  if (linkType !== undefined && linkType !== null && linkType !== "social" && linkType !== "payment") {
    return Errors.badRequest('link_type must be "social" or "payment"', "invalid_link_type");
  }

  const result = await runLinkOperation(() =>
    upsertDraftLink(ctx.env, {
      draftId,
      ownerUserId: common.ownerUserId,
      expectedRevision: common.expectedRevision,
      linkId,
      platform: parseOptionalString(body!.platform),
      publicLabel,
      username: parseOptionalString(body!.username),
      normalizedUrl,
      linkType: (linkType as "social" | "payment" | undefined) ?? null,
      enabled: body!.enabled === undefined ? true : body!.enabled === true,
      preferred: parseOptionalBoolean(body!.preferred),
    }),
  );
  if (!result.ok) return result.response;
  return ok({ draft: serializeDraftContract(result.value) });
}

/** `DELETE /v1/profile-drafts/:draftId/links/:linkId` -- remove a manual link. */
export async function handleDeleteLink(ctx: RouteContext): Promise<Response> {
  const draftId = ctx.params.draftId ?? "";
  const linkId = ctx.params.linkId ?? "";
  const body = await readJsonBody(ctx.request);
  const common = parseCommonMutationFields(body);
  if (common instanceof Response) return common;

  const result = await runLinkOperation(() =>
    removeDraftLink(ctx.env, {
      draftId,
      ownerUserId: common.ownerUserId,
      expectedRevision: common.expectedRevision,
      linkId,
    }),
  );
  if (!result.ok) return result.response;
  return ok({ draft: serializeDraftContract(result.value) });
}
