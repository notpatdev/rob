/**
 * Bridges the profile system's Throne connection into the legacy
 * `domme_registrations` projection the webhook fan-out actually reads.
 *
 * `domme_registrations` predates profiles entirely: it is what the old
 * `/register` flow writes, and it is still the *only* table
 * `webhookThrone.ts` consults to decide which guilds get notified. Rather
 * than teaching the webhook path to understand profiles at all, publishing
 * a connected Dom/me/switch profile (or completing `/bill setup` for a
 * guild that already has one) simply keeps this same projection in sync --
 * "bridged, not replaced", per the profile system's design. This is
 * deliberately a best-effort sync, not part of any atomic publish
 * guarantee: a registration row is only ever added or refreshed here, and
 * only when a guild is already configured and the profile has a connected
 * Throne creator, so a pre-existing registration a user set up by hand is
 * never touched or removed by a profile that has no Throne connection.
 */
import type { Env } from "../env.js";
import { requireHomeGuildId } from "../env.js";
import { newId, nowIso } from "../util/id.js";

interface DocumentThroneRow {
  throne_creator_id: string | null;
}

/**
 * The Throne creator id that governs `guildId` for `ownerUserId`, mirroring
 * (but not reusing) the public resolver's home/independent/linked
 * algorithm -- this reads the raw `throne_creator_id` column the public
 * `ResolvedProfile` type deliberately never exposes, since registration
 * sync is an internal bridge, not a viewer-facing lookup.
 */
async function resolveGoverningThroneCreatorId(env: Env, guildId: string, ownerUserId: string): Promise<string | null> {
  const homeGuildId = requireHomeGuildId(env);

  if (guildId === homeGuildId) {
    const root = await env.DB.prepare("SELECT current_document_id FROM global_profiles WHERE owner_user_id = ?")
      .bind(ownerUserId)
      .first<{ current_document_id: string }>();
    if (root === null) return null;
    const doc = await env.DB.prepare("SELECT throne_creator_id FROM profile_documents WHERE id = ?")
      .bind(root.current_document_id)
      .first<DocumentThroneRow>();
    return doc?.throne_creator_id ?? null;
  }

  const serverRoot = await env.DB.prepare(
    "SELECT mode, current_document_id FROM server_profiles WHERE guild_id = ? AND owner_user_id = ?",
  )
    .bind(guildId, ownerUserId)
    .first<{ mode: "linked" | "independent"; current_document_id: string }>();
  if (serverRoot === null) return null;

  if (serverRoot.mode === "independent") {
    const doc = await env.DB.prepare("SELECT throne_creator_id FROM profile_documents WHERE id = ?")
      .bind(serverRoot.current_document_id)
      .first<DocumentThroneRow>();
    return doc?.throne_creator_id ?? null;
  }

  // Linked overlays never override Throne ownership; it always comes from the live global document.
  const globalRoot = await env.DB.prepare("SELECT current_document_id FROM global_profiles WHERE owner_user_id = ?")
    .bind(ownerUserId)
    .first<{ current_document_id: string }>();
  if (globalRoot === null) return null;
  const doc = await env.DB.prepare("SELECT throne_creator_id FROM profile_documents WHERE id = ?")
    .bind(globalRoot.current_document_id)
    .first<DocumentThroneRow>();
  return doc?.throne_creator_id ?? null;
}

/**
 * Upserts a `domme_registrations` row for `ownerUserId` in `guildId` if (and only if) that guild
 * is configured and the owner's profile there currently has a connected Throne creator. Does
 * nothing at all otherwise -- it never deactivates or deletes a registration a user configured
 * some other way, since the profile system's job here is only to *add* connections it created.
 */
export async function syncRegistrationForGuild(env: Env, guildId: string, ownerUserId: string): Promise<void> {
  const guild = await env.DB.prepare("SELECT guild_id FROM guilds WHERE guild_id = ?").bind(guildId).first();
  if (guild === null) return;

  const creatorId = await resolveGoverningThroneCreatorId(env, guildId, ownerUserId);
  if (creatorId === null) return;

  const now = nowIso();
  await env.DB.prepare(
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
    .bind(newId(), guildId, creatorId, ownerUserId, now, now)
    .run();
}

/** After a global profile publishes, syncs the home guild plus every guild where this owner
 * already has a published `linked` server profile (an `independent` server profile's Throne
 * connection is its own and is synced separately, when *that* draft publishes). */
export async function syncRegistrationsAfterGlobalPublish(env: Env, ownerUserId: string): Promise<void> {
  await syncRegistrationForGuild(env, requireHomeGuildId(env), ownerUserId);

  const { results } = await env.DB.prepare(
    "SELECT guild_id FROM server_profiles WHERE owner_user_id = ? AND mode = 'linked'",
  )
    .bind(ownerUserId)
    .all<{ guild_id: string }>();
  for (const row of results) {
    await syncRegistrationForGuild(env, row.guild_id, ownerUserId);
  }
}

/**
 * Called when `/bill setup` completes for a guild: any user who already
 * has a published (independent or linked) server profile there, or --
 * if this guild is the home guild -- a published global profile, gets its
 * registration materialized now that the guild is finally configured. This
 * is what lets a Dom/me publish a connected profile *before* their guild
 * runs setup without losing that connection once it does.
 */
export async function syncRegistrationsForGuildSetupCompletion(env: Env, guildId: string): Promise<void> {
  const { results: serverOwners } = await env.DB.prepare("SELECT owner_user_id FROM server_profiles WHERE guild_id = ?")
    .bind(guildId)
    .all<{ owner_user_id: string }>();
  for (const row of serverOwners) {
    await syncRegistrationForGuild(env, guildId, row.owner_user_id);
  }

  if (guildId === requireHomeGuildId(env)) {
    const { results: globalOwners } = await env.DB.prepare("SELECT owner_user_id FROM global_profiles").all<{
      owner_user_id: string;
    }>();
    for (const row of globalOwners) {
      await syncRegistrationForGuild(env, guildId, row.owner_user_id);
    }
  }
}
