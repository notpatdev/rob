import type { RouteContext } from "../router.js";
import { Errors, fail, ok } from "../util/response.js";
import { isSnowflake } from "../util/snowflake.js";
import { HomeGuildNotConfiguredError } from "../env.js";
import { STEP_KEYS, type StepKey } from "../profile/contracts.js";
import {
  DraftError,
  applyDraftStep,
  getDraftContract,
  restartDraft,
  startDraft,
  type DraftContract,
} from "../profile/draftService.js";
import { publishDraft } from "../profile/publishService.js";
import type { ResolvedProfile } from "../profile/resolver.js";

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
    throne_prefill:
      draft.thronePrefill === null
        ? null
        : {
            owned_creators: draft.thronePrefill.ownedCreators.map((creator) => ({ id: creator.id, handle: creator.handle })),
            existing_registration_creator_id: draft.thronePrefill.existingRegistrationCreatorId,
          },
    created_at: draft.createdAt,
    updated_at: draft.updatedAt,
    published_at: draft.publishedAt,
  };
}

function serializeResolvedProfile(profile: ResolvedProfile) {
  return {
    scope: profile.scope,
    mode: profile.mode,
    owner_user_id: profile.ownerUserId,
    orientation: profile.orientation,
    dm_status: profile.dmStatus,
    bio: profile.bio,
    public_send_stats: profile.publicSendStats,
    selections: {
      pronouns: profile.selections.pronouns,
      honourifics: profile.selections.honourifics,
      submissive_labels: profile.selections.submissiveLabels,
    },
    aliases: profile.aliases,
    links: profile.links.map((link) => ({
      id: link.id,
      platform: link.platform,
      public_label: link.publicLabel,
      username: link.username,
      normalized_url: link.normalizedUrl,
      link_type: link.linkType,
      sort_order: link.sortOrder,
    })),
    preferred_payment_link_id: profile.preferredPaymentLinkId,
    throne_connected: profile.throneConnected,
    send_stats:
      profile.sendStats === null
        ? null
        : profile.sendStats.map((entry) => ({
            currency: entry.currency,
            count: entry.count,
            total_amount_minor: entry.totalAmountMinor,
          })),
    version: profile.version,
    published_at: profile.publishedAt,
  };
}

/** Runs a draft-service call, translating its typed errors into HTTP responses. */
async function runDraftOperation<T>(operation: () => Promise<T>): Promise<{ ok: true; value: T } | { ok: false; response: Response }> {
  try {
    return { ok: true, value: await operation() };
  } catch (error) {
    if (error instanceof DraftError) {
      return { ok: false, response: fail(error.status, error.code, error.message) };
    }
    if (error instanceof HomeGuildNotConfiguredError) {
      return { ok: false, response: Errors.internal("Worker is not configured with a valid BILL_HOME_GUILD_ID") };
    }
    throw error;
  }
}

export async function handleStartDraft(ctx: RouteContext): Promise<Response> {
  const body = await readJsonBody(ctx.request);
  if (body === null) return Errors.badRequest("Request body must be a JSON object", "invalid_body");

  const ownerUserId = body.owner_user_id;
  const originGuildId = body.origin_guild_id;
  const targetScope = body.target_scope;
  if (!isSnowflake(ownerUserId)) return Errors.badRequest("owner_user_id must be a Discord snowflake", "invalid_owner_user_id");
  if (!isSnowflake(originGuildId)) return Errors.badRequest("origin_guild_id must be a Discord snowflake", "invalid_origin_guild_id");
  if (targetScope !== "global" && targetScope !== "server") {
    return Errors.badRequest('target_scope must be "global" or "server"', "invalid_target_scope");
  }

  let guildId: string | null = null;
  let serverMode: "linked" | "independent" | null = null;
  if (targetScope === "server") {
    if (!isSnowflake(body.guild_id)) return Errors.badRequest("guild_id must be a Discord snowflake", "invalid_guild_id");
    if (body.server_mode !== "linked" && body.server_mode !== "independent") {
      return Errors.badRequest('server_mode must be "linked" or "independent"', "invalid_server_mode");
    }
    guildId = body.guild_id;
    serverMode = body.server_mode;
  }

  const result = await runDraftOperation(() =>
    startDraft(ctx.env, { ownerUserId, originGuildId, targetScope, guildId, serverMode }),
  );
  if (!result.ok) return result.response;
  return ok({ resume_required: result.value.resumeRequired, draft: serializeDraftContract(result.value.draft) });
}

export async function handleGetDraft(ctx: RouteContext): Promise<Response> {
  const draftId = ctx.params.draftId ?? "";
  const ownerUserId = new URL(ctx.request.url).searchParams.get("owner_user_id");
  if (!isSnowflake(ownerUserId)) return Errors.badRequest("owner_user_id must be a Discord snowflake", "invalid_owner_user_id");

  const result = await runDraftOperation(() => getDraftContract(ctx.env, draftId, ownerUserId));
  if (!result.ok) return result.response;
  return ok({ draft: serializeDraftContract(result.value) });
}

export async function handlePutDraftStep(ctx: RouteContext): Promise<Response> {
  const draftId = ctx.params.draftId ?? "";
  const stepKey = ctx.params.stepKey ?? "";
  if (!(STEP_KEYS as readonly string[]).includes(stepKey)) {
    return Errors.badRequest(`stepKey must be one of: ${STEP_KEYS.join(", ")}`, "invalid_step_key");
  }

  const body = await readJsonBody(ctx.request);
  if (body === null) return Errors.badRequest("Request body must be a JSON object", "invalid_body");

  const ownerUserId = body.owner_user_id;
  if (!isSnowflake(ownerUserId)) return Errors.badRequest("owner_user_id must be a Discord snowflake", "invalid_owner_user_id");
  const expectedRevision = body.expected_revision;
  if (typeof expectedRevision !== "number" || !Number.isInteger(expectedRevision) || expectedRevision < 0) {
    return Errors.badRequest("expected_revision must be a non-negative integer", "invalid_expected_revision");
  }

  const result = await runDraftOperation(() =>
    applyDraftStep(ctx.env, {
      draftId,
      stepKey: stepKey as StepKey,
      ownerUserId,
      expectedRevision,
      body,
    }),
  );
  if (!result.ok) return result.response;
  return ok({ draft: serializeDraftContract(result.value) });
}

interface DraftMutationBody {
  ownerUserId: string;
  expectedRevision: number;
}

function parseDraftMutationBody(body: Record<string, unknown> | null): DraftMutationBody | Response {
  if (body === null) return Errors.badRequest("Request body must be a JSON object", "invalid_body");
  const ownerUserId = body.owner_user_id;
  if (!isSnowflake(ownerUserId)) return Errors.badRequest("owner_user_id must be a Discord snowflake", "invalid_owner_user_id");
  const expectedRevision = body.expected_revision;
  if (typeof expectedRevision !== "number" || !Number.isInteger(expectedRevision) || expectedRevision < 0) {
    return Errors.badRequest("expected_revision must be a non-negative integer", "invalid_expected_revision");
  }
  return { ownerUserId, expectedRevision };
}

export async function handleRestartDraft(ctx: RouteContext): Promise<Response> {
  const draftId = ctx.params.draftId ?? "";
  const parsed = parseDraftMutationBody(await readJsonBody(ctx.request));
  if (parsed instanceof Response) return parsed;

  const result = await runDraftOperation(() =>
    restartDraft(ctx.env, { draftId, ownerUserId: parsed.ownerUserId, expectedRevision: parsed.expectedRevision }),
  );
  if (!result.ok) return result.response;
  return ok({ draft: serializeDraftContract(result.value) });
}

export async function handlePublishDraft(ctx: RouteContext): Promise<Response> {
  const draftId = ctx.params.draftId ?? "";
  const parsed = parseDraftMutationBody(await readJsonBody(ctx.request));
  if (parsed instanceof Response) return parsed;

  const result = await runDraftOperation(() =>
    publishDraft(ctx.env, { draftId, ownerUserId: parsed.ownerUserId, expectedRevision: parsed.expectedRevision }),
  );
  if (!result.ok) return result.response;
  return ok({ profile: serializeResolvedProfile(result.value) });
}
