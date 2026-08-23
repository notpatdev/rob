import type { RouteContext } from "../router.js";
import { Errors, fail, ok } from "../util/response.js";
import { isSnowflake } from "../util/snowflake.js";
import { HomeGuildNotConfiguredError } from "../env.js";
import { DraftError, type DraftContract } from "../profile/draftService.js";
import { ValidationError } from "../profile/contracts.js";
import { confirmLinkImport, createLinkImport, type ImportContract } from "../profile/linkImportService.js";

function serializeDraftContract(draft: DraftContract) {
  return {
    id: draft.id,
    revision: draft.revision,
    current_step: draft.currentStep,
    next_step: draft.nextStep,
    steps: draft.steps.map((step) => ({ key: step.key, status: step.status, completed_at: step.completedAt })),
    dm_status_selected: draft.dmStatusSelected,
    document: {
      links: draft.document.links.map((link) => ({
        id: link.id,
        platform: link.platform,
        public_label: link.publicLabel,
        username: link.username,
        normalized_url: link.normalizedUrl,
        link_type: link.linkType,
        enabled: link.enabled,
      })),
      preferred_payment_link_id: draft.document.preferredPaymentLinkId,
    },
    updated_at: draft.updatedAt,
  };
}

function serializeImportContract(importContract: ImportContract) {
  return {
    id: importContract.id,
    draft_id: importContract.draftId,
    source_url: importContract.sourceUrl,
    provider: importContract.provider,
    status: importContract.status,
    candidates: importContract.candidates.map((candidate) => ({
      id: candidate.id,
      platform: candidate.platform,
      public_label: candidate.publicLabel,
      username: candidate.username,
      normalized_url: candidate.normalizedUrl,
      link_type: candidate.linkType,
      selected: candidate.selected,
    })),
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

async function runImportOperation<T>(
  operation: () => Promise<T>,
): Promise<{ ok: true; value: T } | { ok: false; response: Response }> {
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

/** `POST /v1/profile-drafts/:draftId/link-imports` -- fetch a link page under the SSRF-defended
 * importer and store its candidates (or a `blocked`/`fetch_failed`/`no_links_found` outcome). */
export async function handleCreateLinkImport(ctx: RouteContext): Promise<Response> {
  const draftId = ctx.params.draftId ?? "";
  const body = await readJsonBody(ctx.request);
  const common = parseCommonMutationFields(body);
  if (common instanceof Response) return common;

  const sourceUrl = body!.source_url;
  if (typeof sourceUrl !== "string" || sourceUrl.trim().length === 0) {
    return Errors.badRequest("source_url must be a non-empty string", "invalid_field");
  }

  const result = await runImportOperation(() =>
    createLinkImport(ctx.env, {
      draftId,
      ownerUserId: common.ownerUserId,
      expectedRevision: common.expectedRevision,
      sourceUrl,
    }),
  );
  if (!result.ok) return result.response;
  return ok(
    {
      import: serializeImportContract(result.value.importContract),
      draft: serializeDraftContract(result.value.draft),
    },
    201,
  );
}

/** `POST /v1/profile-drafts/:draftId/link-imports/:importId/confirm` -- "Looks Good!": promote
 * selected candidates into the draft's own links, atomically. */
export async function handleConfirmLinkImport(ctx: RouteContext): Promise<Response> {
  const draftId = ctx.params.draftId ?? "";
  const importId = ctx.params.importId ?? "";
  const body = await readJsonBody(ctx.request);
  const common = parseCommonMutationFields(body);
  if (common instanceof Response) return common;

  const candidateIdsRaw = body!.candidate_ids;
  let candidateIds: string[] | null = null;
  if (candidateIdsRaw !== undefined && candidateIdsRaw !== null) {
    if (!Array.isArray(candidateIdsRaw) || candidateIdsRaw.some((id) => typeof id !== "string")) {
      return Errors.badRequest("candidate_ids must be an array of strings", "invalid_field");
    }
    candidateIds = candidateIdsRaw;
  }

  const result = await runImportOperation(() =>
    confirmLinkImport(ctx.env, {
      draftId,
      importId,
      ownerUserId: common.ownerUserId,
      expectedRevision: common.expectedRevision,
      candidateIds,
    }),
  );
  if (!result.ok) return result.response;
  return ok({
    draft: serializeDraftContract(result.value.draft),
    added_link_count: result.value.addedLinkCount,
    skipped_duplicate_count: result.value.skippedDuplicateCount,
  });
}
