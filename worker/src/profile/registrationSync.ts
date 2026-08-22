/**
 * Builds the legacy `domme_registrations` projection consumed by Throne
 * webhook fan-out.
 *
 * Profiles do not replace that v1 table: publication and guild setup project
 * effective profile ownership into it in the same D1 batch as their own
 * revision change. Rows created here carry `profile_managed = 1`; explicit v1
 * registrations retain `profile_managed = 0` and are never overwritten or
 * deactivated. Keeping the projection statements guardable is important
 * because D1 batches are atomic, but a stale zero-row UPDATE is not an error.
 */
import type { Env } from "../env.js";
import { requireHomeGuildId } from "../env.js";
import { nowIso } from "../util/id.js";

export interface RegistrationProjection {
  readonly guildId: string;
  readonly ownerUserId: string;
  readonly creatorId: string | null;
}

export interface RegistrationSqlGuard {
  readonly sql: string;
  readonly params: readonly unknown[];
}

function projectionValuesSql(projections: readonly RegistrationProjection[]): string {
  return projections.map(() => "(?, ?, ?)").join(", ");
}

function projectionParams(projections: readonly RegistrationProjection[]): unknown[] {
  return projections.flatMap((projection) => [
    projection.guildId,
    projection.ownerUserId,
    projection.creatorId,
  ]);
}

/**
 * Produces two CAS-compatible statements: deactivate a superseded
 * profile-managed row, then materialize the new effective creator. SQLite's
 * two uniqueness constraints (guild+user and guild+creator) are both handled,
 * while conflict clauses deliberately refuse to mutate legacy rows.
 */
export function buildRegistrationProjectionStatements(
  env: Env,
  projections: readonly RegistrationProjection[],
  guard: RegistrationSqlGuard = { sql: "1 = 1", params: [] },
): D1PreparedStatement[] {
  if (projections.length === 0) return [];

  const valuesSql = projectionValuesSql(projections);
  const params = projectionParams(projections);
  const now = nowIso();
  return [
    env.DB.prepare(
      `WITH projections(guild_id, owner_user_id, creator_id) AS (VALUES ${valuesSql})
       UPDATE domme_registrations
          SET active = 0, updated_at = ?
        WHERE profile_managed = 1
          AND EXISTS (
            SELECT 1 FROM projections p
             WHERE p.guild_id = domme_registrations.guild_id
               AND p.owner_user_id = domme_registrations.discord_user_id
               AND (p.creator_id IS NULL OR p.creator_id <> domme_registrations.creator_id)
          )
          AND ${guard.sql}`,
    ).bind(...params, now, ...guard.params),
    env.DB.prepare(
      `WITH projections(guild_id, owner_user_id, creator_id) AS (VALUES ${valuesSql})
       INSERT INTO domme_registrations
         (id, guild_id, creator_id, discord_user_id, active, profile_managed, created_at, updated_at)
       SELECT lower(hex(randomblob(16))), p.guild_id, p.creator_id, p.owner_user_id, 1, 1, ?, ?
         FROM projections p
        WHERE p.creator_id IS NOT NULL AND ${guard.sql}
       ON CONFLICT (guild_id, discord_user_id) DO UPDATE SET
         creator_id = excluded.creator_id,
         active = 1,
         profile_managed = 1,
         updated_at = excluded.updated_at
       WHERE domme_registrations.profile_managed = 1
       ON CONFLICT (guild_id, creator_id) DO UPDATE SET
         discord_user_id = excluded.discord_user_id,
         active = 1,
         profile_managed = 1,
         updated_at = excluded.updated_at
       WHERE domme_registrations.profile_managed = 1`,
    ).bind(...params, now, now, ...guard.params),
  ];
}

/** SQL predicate used as a publication tripwire. A profile choosing a creator
 * that disagrees with an explicit v1 registration must surface a conflict,
 * never silently appear published while webhooks still use different data. */
export function legacyRegistrationConflictGuard(
  projections: readonly RegistrationProjection[],
): RegistrationSqlGuard {
  if (projections.length === 0) return { sql: "1 = 1", params: [] };
  const valuesSql = projectionValuesSql(projections);
  return {
    sql: `NOT EXISTS (
      WITH projections(guild_id, owner_user_id, creator_id) AS (VALUES ${valuesSql})
      SELECT 1
        FROM domme_registrations r
        JOIN projections p ON p.guild_id = r.guild_id
       WHERE r.profile_managed = 0 AND r.active = 1 AND p.creator_id IS NOT NULL
         AND (
           (r.discord_user_id = p.owner_user_id AND r.creator_id <> p.creator_id)
           OR (r.creator_id = p.creator_id AND r.discord_user_id <> p.owner_user_id)
         )
    )`,
    params: projectionParams(projections),
  };
}

export async function hasLegacyRegistrationConflict(
  env: Env,
  projections: readonly RegistrationProjection[],
): Promise<boolean> {
  const guard = legacyRegistrationConflictGuard(projections);
  const row = await env.DB.prepare(`SELECT (${guard.sql}) AS allowed`)
    .bind(...guard.params)
    .first<{ allowed: number }>();
  return row?.allowed !== 1;
}

/** Determines every configured guild whose effective Throne connection changes
 * when one draft publishes. Global publication affects home plus linked
 * overlays; independent server publication affects only its own guild. */
export async function collectPublishRegistrationProjections(
  env: Env,
  input: {
    readonly targetScope: "global" | "server";
    readonly guildId: string | null;
    readonly serverMode: "linked" | "independent" | null;
    readonly ownerUserId: string;
    readonly creatorId: string | null;
  },
): Promise<RegistrationProjection[]> {
  if (input.targetScope === "server") {
    const configured = await env.DB.prepare("SELECT guild_id FROM guilds WHERE guild_id = ?")
      .bind(input.guildId)
      .first<{ guild_id: string }>();
    return configured === null
      ? []
      : [{ guildId: configured.guild_id, ownerUserId: input.ownerUserId, creatorId: input.creatorId }];
  }

  const homeGuildId = requireHomeGuildId(env);
  const { results } = await env.DB.prepare(
    `SELECT guild_id FROM guilds
      WHERE guild_id = ?
         OR guild_id IN (
           SELECT guild_id FROM server_profiles
            WHERE owner_user_id = ? AND mode = 'linked'
         )`,
  )
    .bind(homeGuildId, input.ownerUserId)
    .all<{ guild_id: string }>();
  return results.map((row) => ({
    guildId: row.guild_id,
    ownerUserId: input.ownerUserId,
    creatorId: input.creatorId,
  }));
}

/** Reads effective published profile ownership before `/bill setup` creates
 * the guild row, so all safe projections can join the completion batch. */
export async function collectGuildSetupRegistrationProjections(
  env: Env,
  guildId: string,
): Promise<RegistrationProjection[]> {
  const { results } = await env.DB.prepare(
    `SELECT sp.owner_user_id,
            CASE WHEN sp.mode = 'linked' THEN gd.throne_creator_id ELSE sd.throne_creator_id END AS creator_id
       FROM server_profiles sp
       JOIN profile_documents sd ON sd.id = sp.current_document_id
       LEFT JOIN global_profiles gp ON gp.owner_user_id = sp.owner_user_id
       LEFT JOIN profile_documents gd ON gd.id = gp.current_document_id
      WHERE sp.guild_id = ?
      UNION
     SELECT gp.owner_user_id, gd.throne_creator_id
       FROM global_profiles gp
       JOIN profile_documents gd ON gd.id = gp.current_document_id
      WHERE ? = ?`,
  )
    .bind(guildId, guildId, requireHomeGuildId(env))
    .all<{ owner_user_id: string; creator_id: string | null }>();
  return results.map((row) => ({
    guildId,
    ownerUserId: row.owner_user_id,
    creatorId: row.creator_id,
  }));
}

/** Compatibility helper for maintenance/tests outside publication. New write
 * paths should append the prepared statements to their own guarded batch. */
export async function syncRegistrationForGuild(
  env: Env,
  guildId: string,
  ownerUserId: string,
): Promise<void> {
  const projections = await collectGuildSetupRegistrationProjections(env, guildId);
  const projection = projections.find((item) => item.ownerUserId === ownerUserId);
  if (projection === undefined) return;
  await env.DB.batch(buildRegistrationProjectionStatements(env, [projection]));
}
