import type { RouteContext } from "../router.js";
import { Errors, ok } from "../util/response.js";
import { isSnowflake } from "../util/snowflake.js";
import { HomeGuildNotConfiguredError } from "../env.js";
import { resolveProfile, type ResolvedProfile } from "../profile/resolver.js";

function serializeProfile(profile: ResolvedProfile) {
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

/** `GET /v1/guilds/:guildId/profiles/:userId` -- the one read path every viewer/bot surface uses. */
export async function handleGetProfile(ctx: RouteContext): Promise<Response> {
  const guildId = ctx.params.guildId ?? "";
  const userId = ctx.params.userId ?? "";
  if (!isSnowflake(guildId)) return Errors.badRequest("guildId must be a Discord snowflake", "invalid_guild_id");
  if (!isSnowflake(userId)) return Errors.badRequest("userId must be a Discord snowflake", "invalid_user_id");

  try {
    const { profile, globalAvailable } = await resolveProfile(ctx.env, guildId, userId);
    return ok({
      profile: profile === null ? null : serializeProfile(profile),
      global_available: globalAvailable,
    });
  } catch (error) {
    if (error instanceof HomeGuildNotConfiguredError) {
      return Errors.internal("Worker is not configured with a valid BILL_HOME_GUILD_ID");
    }
    throw error;
  }
}
