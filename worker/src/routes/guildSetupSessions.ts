import type { RouteContext } from "../router.js";
import { Errors, fail, ok } from "../util/response.js";
import { isSnowflake } from "../util/snowflake.js";
import { HomeGuildNotConfiguredError } from "../env.js";
import {
  GuildSetupError,
  completeGuildSetupSession,
  createGuildSetupSession,
  getGuildSetupSession,
  setGuildSetupChannel,
  type GuildSetupSessionContract,
} from "../profile/guildSetupService.js";

function serializeSession(session: GuildSetupSessionContract) {
  return {
    id: session.id,
    guild_id: session.guildId,
    initiator_user_id: session.initiatorUserId,
    status: session.status,
    current_step: session.currentStep,
    selected_channel_id: session.selectedChannelId,
    revision: session.revision,
    public_message_id: session.publicMessageId,
    expires_at: session.expiresAt,
    created_at: session.createdAt,
    updated_at: session.updatedAt,
    completed_at: session.completedAt,
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

async function runSetupOperation<T>(
  operation: () => Promise<T>,
): Promise<{ ok: true; value: T } | { ok: false; response: Response }> {
  try {
    return { ok: true, value: await operation() };
  } catch (error) {
    if (error instanceof GuildSetupError) return { ok: false, response: fail(error.status, error.code, error.message) };
    if (error instanceof HomeGuildNotConfiguredError) {
      return { ok: false, response: Errors.internal("Worker is not configured with a valid BILL_HOME_GUILD_ID") };
    }
    throw error;
  }
}

function parseCallbackFields(
  body: Record<string, unknown> | null,
): { guildId: string; initiatorUserId: string; expectedRevision: number } | Response {
  if (body === null) return Errors.badRequest("Request body must be a JSON object", "invalid_body");
  const guildId = body.guild_id;
  if (!isSnowflake(guildId)) return Errors.badRequest("guild_id must be a Discord snowflake", "invalid_guild_id");
  const initiatorUserId = body.initiator_user_id;
  if (!isSnowflake(initiatorUserId)) {
    return Errors.badRequest("initiator_user_id must be a Discord snowflake", "invalid_initiator_user_id");
  }
  const expectedRevision = body.expected_revision;
  if (typeof expectedRevision !== "number" || !Number.isInteger(expectedRevision) || expectedRevision < 0) {
    return Errors.badRequest("expected_revision must be a non-negative integer", "invalid_expected_revision");
  }
  return { guildId, initiatorUserId, expectedRevision };
}

/** `POST /v1/guild-setup-sessions` -- start (or resume the guild's one active) setup session. */
export async function handleCreateGuildSetupSession(ctx: RouteContext): Promise<Response> {
  const body = await readJsonBody(ctx.request);
  if (body === null) return Errors.badRequest("Request body must be a JSON object", "invalid_body");

  const guildId = body.guild_id;
  const initiatorUserId = body.initiator_user_id;
  if (!isSnowflake(guildId)) return Errors.badRequest("guild_id must be a Discord snowflake", "invalid_guild_id");
  if (!isSnowflake(initiatorUserId)) {
    return Errors.badRequest("initiator_user_id must be a Discord snowflake", "invalid_initiator_user_id");
  }

  const result = await runSetupOperation(() => createGuildSetupSession(ctx.env, { guildId, initiatorUserId }));
  if (!result.ok) return result.response;
  return ok({ resume_required: result.value.resumeRequired, session: serializeSession(result.value.session) });
}

/** `GET /v1/guild-setup-sessions/:sessionId` -- read current session state (used to reconstruct
 * the public message, e.g. after a bot restart); no initiator check, since any moderator with the
 * session id (only ever shared via the bot's own message) may need to observe its state. */
export async function handleGetGuildSetupSession(ctx: RouteContext): Promise<Response> {
  const sessionId = ctx.params.sessionId ?? "";
  const result = await runSetupOperation(() => getGuildSetupSession(ctx.env, sessionId));
  if (!result.ok) return result.response;
  return ok({ session: serializeSession(result.value) });
}

/** `PUT /v1/guild-setup-sessions/:sessionId/channel` -- record the chosen channel. */
export async function handleSetGuildSetupChannel(ctx: RouteContext): Promise<Response> {
  const sessionId = ctx.params.sessionId ?? "";
  const body = await readJsonBody(ctx.request);
  const common = parseCallbackFields(body);
  if (common instanceof Response) return common;

  const channelId = body!.channel_id;
  if (!isSnowflake(channelId)) return Errors.badRequest("channel_id must be a Discord snowflake", "invalid_channel_id");

  const result = await runSetupOperation(() =>
    setGuildSetupChannel(ctx.env, {
      sessionId,
      guildId: common.guildId,
      initiatorUserId: common.initiatorUserId,
      expectedRevision: common.expectedRevision,
      channelId,
    }),
  );
  if (!result.ok) return result.response;
  return ok({ session: serializeSession(result.value) });
}

/** `POST /v1/guild-setup-sessions/:sessionId/complete` -- atomically persists the guild's config
 * and bridges any already-published connected profiles in that guild. */
export async function handleCompleteGuildSetupSession(ctx: RouteContext): Promise<Response> {
  const sessionId = ctx.params.sessionId ?? "";
  const body = await readJsonBody(ctx.request);
  const common = parseCallbackFields(body);
  if (common instanceof Response) return common;

  const result = await runSetupOperation(() =>
    completeGuildSetupSession(ctx.env, {
      sessionId,
      guildId: common.guildId,
      initiatorUserId: common.initiatorUserId,
      expectedRevision: common.expectedRevision,
    }),
  );
  if (!result.ok) return result.response;
  return ok({ session: serializeSession(result.value.session), send_channel_id: result.value.sendChannelId });
}
