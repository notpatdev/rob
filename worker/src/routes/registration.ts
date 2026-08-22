import type { RouteContext } from "../router.js";
import { Errors, ok } from "../util/response.js";
import { isSnowflake } from "../util/snowflake.js";
import { newId, newRouteSecret, nowIso } from "../util/id.js";
import { sha256Hex } from "../util/hash.js";
import { normalizeThroneInput } from "../throne/normalize.js";
import { resolveThroneCreator } from "../throne/resolve.js";

interface CreatorRow {
  id: string;
  public_creator_id: string;
  handle: string;
  owner_discord_user_id: string;
}

function buildWebhookUrl(publicBaseUrl: string, creatorId: string, secret: string): string {
  return `${publicBaseUrl.replace(/\/+$/, "")}/t/${creatorId}/${secret}`;
}

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

  const normalized = normalizeThroneInput(throneInput);
  if (!normalized) {
    return Errors.badRequest("throne must be a Throne username or profile URL", "invalid_throne_input");
  }

  const resolved = await resolveThroneCreator(normalized.username);
  if (!resolved) {
    return Errors.notFound("Could not resolve that Throne creator", "throne_creator_not_found");
  }

  const now = nowIso();
  const existing = await ctx.env.DB.prepare(
    "SELECT id, public_creator_id, handle, owner_discord_user_id FROM throne_creators WHERE public_creator_id = ?",
  )
    .bind(resolved.publicCreatorId)
    .first<CreatorRow>();

  if (existing && existing.owner_discord_user_id !== discordUserId) {
    return Errors.conflict(
      "That Throne creator is already linked by a different Discord user",
      "creator_owned",
    );
  }

  let creatorId: string;
  let throneHandle: string;
  let webhookUrl: string | null = null;
  let webhookState: "issued" | "existing" | "rotated";

  if (!existing) {
    creatorId = newId();
    throneHandle = resolved.handle;
    const secret = newRouteSecret();
    const secretHash = await sha256Hex(secret);
    await ctx.env.DB.prepare(
      `INSERT INTO throne_creators
         (id, public_creator_id, handle, profile_url, route_secret_hash, owner_discord_user_id, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
    )
      .bind(
        creatorId,
        resolved.publicCreatorId,
        resolved.handle,
        normalized.profileUrl,
        secretHash,
        discordUserId,
        now,
        now,
      )
      .run();
    webhookUrl = buildWebhookUrl(ctx.env.PUBLIC_BASE_URL, creatorId, secret);
    webhookState = "issued";
  } else {
    creatorId = existing.id;
    throneHandle = resolved.handle;
    // Keep the cached handle/profile URL fresh; this never touches the secret.
    await ctx.env.DB.prepare(
      "UPDATE throne_creators SET handle = ?, profile_url = ?, updated_at = ? WHERE id = ?",
    )
      .bind(resolved.handle, normalized.profileUrl, now, creatorId)
      .run();

    if (resetWebhook) {
      const secret = newRouteSecret();
      const secretHash = await sha256Hex(secret);
      await ctx.env.DB.prepare("UPDATE throne_creators SET route_secret_hash = ?, updated_at = ? WHERE id = ?")
        .bind(secretHash, now, creatorId)
        .run();
      webhookUrl = buildWebhookUrl(ctx.env.PUBLIC_BASE_URL, creatorId, secret);
      webhookState = "rotated";
    } else {
      webhookState = "existing";
    }
  }

  // Re-registering the same Discord user in the same guild must cleanly
  // update their creator association rather than conflicting; re-linking the
  // same creator to a different Discord user in the same guild also updates
  // in place. Both uniqueness constraints get their own ON CONFLICT clause
  // so SQLite picks whichever one the actual violation matches.
  await ctx.env.DB.prepare(
    `INSERT INTO domme_registrations (id, guild_id, creator_id, discord_user_id, active, created_at, updated_at)
     VALUES (?, ?, ?, ?, 1, ?, ?)
     ON CONFLICT (guild_id, discord_user_id) DO UPDATE SET
       creator_id = excluded.creator_id,
       active = 1,
       updated_at = excluded.updated_at
     ON CONFLICT (guild_id, creator_id) DO UPDATE SET
       discord_user_id = excluded.discord_user_id,
       active = 1,
       updated_at = excluded.updated_at`,
  )
    .bind(newId(), guildId, creatorId, discordUserId, now, now)
    .run();

  return ok({
    creator_id: creatorId,
    throne_handle: throneHandle,
    webhook_url: webhookUrl,
    webhook_state: webhookState,
  });
}
