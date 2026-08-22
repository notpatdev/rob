/**
 * Webhook-time alias attribution: resolves a Throne gift's sender
 * name(s) to at most one Discord user in the *recipient guild's* effective
 * alias set, so future sends can be linked back to a submissive/switch
 * profile's own stats without ever asking a sender to prove who they are.
 *
 * "Effective" mirrors exactly what the public resolver shows a viewer in
 * that guild: the home guild's global profiles, or per-guild independent
 * profiles and linked overlays (which use their own aliases only when the
 * `aliases` field is explicitly overridden, otherwise the live global
 * document's). Multiple different owners can legitimately pick the same
 * alias text (aliases are only unique *within* one document), so this
 * tracks every owner a normalized alias maps to and only attributes when
 * the sender's name(s) match exactly one owner across the whole guild --
 * an ambiguous match is treated the same as no match at all, favoring
 * silence over a wrong attribution.
 */
import type { Env } from "../env.js";
import { requireHomeGuildId } from "../env.js";
import { normalizeAlias } from "./contracts.js";

interface AliasOwnerRow {
  normalized_alias: string;
  owner_user_id: string;
}

async function loadHomeGuildAliasOwners(env: Env): Promise<AliasOwnerRow[]> {
  const { results } = await env.DB.prepare(
    `SELECT a.normalized_alias, gp.owner_user_id
       FROM global_profiles gp
       JOIN profile_aliases a ON a.document_id = gp.current_document_id`,
  ).all<AliasOwnerRow>();
  return results;
}

interface LinkedRootRow {
  owner_user_id: string;
  overlay_document_id: string;
  global_document_id: string;
}

async function loadServerGuildAliasOwners(env: Env, guildId: string): Promise<AliasOwnerRow[]> {
  const independent = await env.DB.prepare(
    `SELECT a.normalized_alias, sp.owner_user_id
       FROM server_profiles sp
       JOIN profile_aliases a ON a.document_id = sp.current_document_id
      WHERE sp.guild_id = ? AND sp.mode = 'independent'`,
  )
    .bind(guildId)
    .all<AliasOwnerRow>();

  const linkedRoots = await env.DB.prepare(
    `SELECT sp.owner_user_id AS owner_user_id,
            sp.current_document_id AS overlay_document_id,
            gp.current_document_id AS global_document_id
       FROM server_profiles sp
       JOIN global_profiles gp ON gp.owner_user_id = sp.owner_user_id
      WHERE sp.guild_id = ? AND sp.mode = 'linked'`,
  )
    .bind(guildId)
    .all<LinkedRootRow>();

  const rows: AliasOwnerRow[] = [...independent.results];
  for (const root of linkedRoots.results) {
    // Same rule the public resolver applies: the overlay's own aliases win only when explicitly
    // overridden, otherwise the live global document's aliases apply -- read fresh, never copied.
    const overridden = await env.DB.prepare(
      "SELECT 1 FROM profile_document_overrides WHERE document_id = ? AND field_name = 'aliases'",
    )
      .bind(root.overlay_document_id)
      .first();
    const sourceDocumentId = overridden !== null ? root.overlay_document_id : root.global_document_id;
    const { results: aliasRows } = await env.DB.prepare(
      "SELECT normalized_alias FROM profile_aliases WHERE document_id = ?",
    )
      .bind(sourceDocumentId)
      .all<{ normalized_alias: string }>();
    for (const aliasRow of aliasRows) {
      rows.push({ normalized_alias: aliasRow.normalized_alias, owner_user_id: root.owner_user_id });
    }
  }
  return rows;
}

/** Builds "normalized alias -> owning Discord user id(s)" for every profile effectively visible
 * in `guildId`, tracking every owner per alias (not just the first) so ambiguity can be detected. */
async function buildEffectiveAliasIndex(env: Env, guildId: string): Promise<Map<string, Set<string>>> {
  const homeGuildId = requireHomeGuildId(env);
  const rows = guildId === homeGuildId ? await loadHomeGuildAliasOwners(env) : await loadServerGuildAliasOwners(env, guildId);

  const index = new Map<string, Set<string>>();
  for (const row of rows) {
    const owners = index.get(row.normalized_alias) ?? new Set<string>();
    owners.add(row.owner_user_id);
    index.set(row.normalized_alias, owners);
  }
  return index;
}

/**
 * Resolves the Discord user a Throne gift's sender name(s) unambiguously
 * match against `guildId`'s effective aliases, or `null` if there is no
 * match or the match is ambiguous. Callers must not invoke this for
 * private/anonymous events -- by the time a webhook payload reaches this
 * point, the Throne event parser has already nulled both sender fields for
 * those, so there is nothing here to match against and the caller's usual
 * "both fields null" guard keeps this from ever running for them.
 */
export async function resolveSenderDiscordUserId(
  env: Env,
  guildId: string,
  rawSenderUsername: string | null,
  rawSenderDisplayName: string | null,
): Promise<string | null> {
  const candidates = [rawSenderUsername, rawSenderDisplayName]
    .filter((value): value is string => typeof value === "string" && value.trim().length > 0)
    .map(normalizeAlias);
  if (candidates.length === 0) return null;

  const index = await buildEffectiveAliasIndex(env, guildId);
  const matchedOwners = new Set<string>();
  for (const candidate of candidates) {
    for (const owner of index.get(candidate) ?? []) matchedOwners.add(owner);
  }
  return matchedOwners.size === 1 ? [...matchedOwners][0]! : null;
}
