import type { RouteContext } from "../router.js";
import { Errors, fail, ok } from "../util/response.js";
import { isSnowflake } from "../util/snowflake.js";
import { newId, nowIso } from "../util/id.js";
import { ThroneResolutionError, httpStatusForThroneErrorCode, resolveOrAttachThroneCreator } from "../throne/creatorService.js";

export async function handleRegisterDomme(ctx: RouteContext): Promise<Response> {
  const guildId = ctx.params.guildId ?? "";
  if (!isSnowflake(guildId)) {
    return Errors.badRequest("guildId must be a Discord snowflake", "invalid_guild_id");
  }

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

  const discordUserId = record.discord_user_id;
  if (!isSnowflake(discordUserId)) {
    return Errors.badRequest("discord_user_id must be a Discord snowflake", "invalid_discord_user_id");
  }

  const throneInput = record.throne;
  if (typeof throneInput !== "string" || throneInput.trim().length === 0) {
    return Errors.badRequest("throne must be a non-empty string", "invalid_throne_input");
  }
  const resetWebhook = record.reset_webhook === true;

  const guild = await ctx.env.DB.prepare("SELECT guild_id FROM guilds WHERE guild_id = ?")
    .bind(guildId)
    .first();
  if (!guild) {
    return Errors.notFound("Run /bill setup before registering a Dom/me", "guild_not_configured");
  }

  let attached: Awaited<ReturnType<typeof resolveOrAttachThroneCreator>>;
  try {
    attached = await resolveOrAttachThroneCreator(ctx.env, discordUserId, throneInput, { rotateWebhook: resetWebhook });
  } catch (error) {
    if (error instanceof ThroneResolutionError) {
      return fail(httpStatusForThroneErrorCode(error.code), error.code, error.message);
    }
    throw error;
  }

  const now = nowIso();
  // Re-registering the same Discord user in the same guild must cleanly
  // update their creator association rather than conflicting; re-linking the
  // same creator to a different Discord user in the same guild also updates
  // in place. Both uniqueness constraints get their own ON CONFLICT clause
  // so SQLite picks whichever one the actual violation matches.
  await ctx.env.DB.prepare(
    `INSERT INTO domme_registrations
       (id, guild_id, creator_id, discord_user_id, active, profile_managed, created_at, updated_at)
     VALUES (?, ?, ?, ?, 1, 0, ?, ?)
     ON CONFLICT (guild_id, discord_user_id) DO UPDATE SET
       creator_id = excluded.creator_id,
       active = 1,
       profile_managed = 0,
       updated_at = excluded.updated_at
     ON CONFLICT (guild_id, creator_id) DO UPDATE SET
       discord_user_id = excluded.discord_user_id,
       active = 1,
       profile_managed = 0,
       updated_at = excluded.updated_at`,
  )
    .bind(newId(), guildId, attached.creatorId, discordUserId, now, now)
    .run();

  return ok({
    creator_id: attached.creatorId,
    throne_handle: attached.handle,
    webhook_url: attached.webhookUrl,
    webhook_state: attached.webhookState,
  });
}
