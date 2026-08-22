import type { RouteContext } from "../router.js";
import { Errors, ok } from "../util/response.js";
import { isSnowflake } from "../util/snowflake.js";
import { nowIso } from "../util/id.js";

interface GuildRow {
  guild_id: string;
  send_channel_id: string;
}

export async function handleGetGuildConfig(ctx: RouteContext): Promise<Response> {
  const guildId = ctx.params.guildId ?? "";
  if (!isSnowflake(guildId)) {
    return Errors.badRequest("guildId must be a Discord snowflake", "invalid_guild_id");
  }

  const row = await ctx.env.DB.prepare("SELECT guild_id, send_channel_id FROM guilds WHERE guild_id = ?")
    .bind(guildId)
    .first<GuildRow>();
  if (!row) return Errors.notFound("Guild is not configured", "guild_not_configured");

  return ok({ guild_id: row.guild_id, send_channel_id: row.send_channel_id });
}

export async function handlePutGuildConfig(ctx: RouteContext): Promise<Response> {
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
  const sendChannelId = (body as Record<string, unknown>).send_channel_id;
  if (!isSnowflake(sendChannelId)) {
    return Errors.badRequest("send_channel_id must be a Discord snowflake", "invalid_send_channel_id");
  }

  const now = nowIso();
  await ctx.env.DB.prepare(
    `INSERT INTO guilds (guild_id, send_channel_id, created_at, updated_at)
     VALUES (?, ?, ?, ?)
     ON CONFLICT (guild_id) DO UPDATE SET
       send_channel_id = excluded.send_channel_id,
       updated_at = excluded.updated_at`,
  )
    .bind(guildId, sendChannelId, now, now)
    .run();

  return ok({ guild_id: guildId, send_channel_id: sendChannelId });
}
