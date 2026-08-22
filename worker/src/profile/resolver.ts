/**
 * Deterministic profile resolution.
 *
 * This is the one place that turns the private, normalized rows in
 * `profile_documents`/`profile_links`/etc. into the public shape a viewer
 * (or the publish-validation path) actually sees. It is intentionally the
 * *only* module that understands the global/linked/independent resolution
 * algorithm, so lookup, publish validation, and any future consumer all
 * agree on the same answer.
 */
import type { Env } from "../env.js";
import { requireHomeGuildId } from "../env.js";
import { OVERRIDABLE_FIELDS, type OverridableField } from "./contracts.js";

export interface ResolvedSelections {
  readonly pronouns: string[];
  readonly honourifics: string[];
  readonly submissiveLabels: string[];
}

export interface ResolvedLink {
  readonly id: string;
  readonly platform: string;
  readonly publicLabel: string;
  readonly username: string | null;
  readonly normalizedUrl: string;
  readonly linkType: "social" | "payment";
  readonly sortOrder: number;
}

export interface SendStatsEntry {
  readonly currency: string;
  readonly count: number;
  readonly totalAmountMinor: number;
}

export interface ResolvedProfile {
  readonly scope: "global" | "server";
  readonly mode: "linked" | "independent" | null;
  readonly ownerUserId: string;
  readonly orientation: string | null;
  readonly dmStatus: string | null;
  readonly bio: string | null;
  readonly publicSendStats: boolean;
  readonly selections: ResolvedSelections;
  readonly aliases: string[];
  readonly links: ResolvedLink[];
  readonly preferredPaymentLinkId: string | null;
  readonly throneConnected: boolean;
  /** Per-currency attributed send counts/totals for *this guild*, present only when
   * `publicSendStats` is enabled -- `null` otherwise (including when the capability/orientation
   * does not support stats at all). Never includes currency conversion. */
  readonly sendStats: readonly SendStatsEntry[] | null;
  readonly version: number;
  readonly publishedAt: string;
}

export interface ProfileLookupResult {
  readonly profile: ResolvedProfile | null;
  /** Whether this user has a published global profile at all, regardless of guild. */
  readonly globalAvailable: boolean;
}

interface DocumentRow {
  id: string;
  owner_user_id: string;
  orientation: string | null;
  dm_status: string | null;
  bio: string | null;
  public_send_stats: number;
  throne_creator_id: string | null;
  preferred_payment_link_id: string | null;
}

interface SelectionRow {
  category: "pronoun" | "honourific" | "submissive_label";
  value: string;
  sort_order: number;
}

interface AliasRow {
  display_alias: string;
  sort_order: number;
}

interface LinkRow {
  id: string;
  platform: string;
  public_label: string;
  username: string | null;
  normalized_url: string;
  link_type: "social" | "payment";
  sort_order: number;
  enabled: number;
}

async function loadDocument(env: Env, documentId: string): Promise<DocumentRow | null> {
  return env.DB.prepare(
    `SELECT id, owner_user_id, orientation, dm_status, bio, public_send_stats, throne_creator_id, preferred_payment_link_id
       FROM profile_documents WHERE id = ?`,
  )
    .bind(documentId)
    .first<DocumentRow>();
}

async function loadSelections(env: Env, documentId: string): Promise<ResolvedSelections> {
  const { results } = await env.DB.prepare(
    "SELECT category, value, sort_order FROM profile_document_selections WHERE document_id = ? ORDER BY category, sort_order, value",
  )
    .bind(documentId)
    .all<SelectionRow>();
  const pronouns: string[] = [];
  const honourifics: string[] = [];
  const submissiveLabels: string[] = [];
  for (const row of results) {
    if (row.category === "pronoun") pronouns.push(row.value);
    else if (row.category === "honourific") honourifics.push(row.value);
    else submissiveLabels.push(row.value);
  }
  return { pronouns, honourifics, submissiveLabels };
}

async function loadAliases(env: Env, documentId: string): Promise<string[]> {
  const { results } = await env.DB.prepare(
    "SELECT display_alias, sort_order FROM profile_aliases WHERE document_id = ? ORDER BY sort_order, display_alias",
  )
    .bind(documentId)
    .all<AliasRow>();
  return results.map((row) => row.display_alias);
}

async function loadLinks(env: Env, documentId: string): Promise<LinkRow[]> {
  const { results } = await env.DB.prepare(
    `SELECT id, platform, public_label, username, normalized_url, link_type, sort_order, enabled
       FROM profile_links WHERE document_id = ? AND enabled = 1 ORDER BY sort_order, id`,
  )
    .bind(documentId)
    .all<LinkRow>();
  return results;
}

function toResolvedLink(row: LinkRow): ResolvedLink {
  return {
    id: row.id,
    platform: row.platform,
    publicLabel: row.public_label,
    username: row.username,
    normalizedUrl: row.normalized_url,
    linkType: row.link_type,
    sortOrder: row.sort_order,
  };
}

/** Builds the fully-resolved shape for a complete (global or independent) document. */
async function resolveCompleteDocument(
  env: Env,
  scope: "global" | "server",
  mode: "linked" | "independent" | null,
  document: DocumentRow,
  version: number,
  publishedAt: string,
): Promise<ResolvedProfile> {
  const [selections, aliases, linkRows] = await Promise.all([
    loadSelections(env, document.id),
    loadAliases(env, document.id),
    loadLinks(env, document.id),
  ]);
  const links = linkRows.map(toResolvedLink).sort(byOrderThenId);
  return {
    scope,
    mode,
    ownerUserId: document.owner_user_id,
    orientation: document.orientation,
    dmStatus: document.dm_status,
    bio: document.bio,
    publicSendStats: document.public_send_stats === 1,
    selections,
    aliases,
    links,
    preferredPaymentLinkId: choosePreferredPayment(links, document.preferred_payment_link_id),
    throneConnected: document.throne_creator_id !== null,
    sendStats: null,
    version,
    publishedAt,
  };
}

function byOrderThenId(a: ResolvedLink, b: ResolvedLink): number {
  if (a.sortOrder !== b.sortOrder) return a.sortOrder - b.sortOrder;
  return a.id.localeCompare(b.id);
}

/**
 * Preferred payment falls back deterministically: the document's own
 * choice if it is still a visible payment link, otherwise the first
 * visible payment link by (sort_order, id). This runs identically for
 * complete documents and for the merged linked-overlay view below.
 */
function choosePreferredPayment(links: readonly ResolvedLink[], preferredId: string | null): string | null {
  const paymentLinks = links.filter((link) => link.linkType === "payment");
  if (preferredId !== null && paymentLinks.some((link) => link.id === preferredId)) {
    return preferredId;
  }
  return paymentLinks[0]?.id ?? null;
}

const OVERRIDABLE_FIELD_SET: ReadonlySet<string> = new Set(OVERRIDABLE_FIELDS);

async function loadOverriddenFields(env: Env, documentId: string): Promise<ReadonlySet<OverridableField>> {
  const { results } = await env.DB.prepare("SELECT field_name FROM profile_document_overrides WHERE document_id = ?")
    .bind(documentId)
    .all<{ field_name: string }>();
  return new Set(
    results.map((row) => row.field_name).filter((field): field is OverridableField => OVERRIDABLE_FIELD_SET.has(field)),
  );
}

async function loadHiddenInheritedLinkIds(env: Env, overlayDocumentId: string): Promise<ReadonlySet<string>> {
  const { results } = await env.DB.prepare(
    "SELECT inherited_link_id FROM profile_link_visibility WHERE document_id = ? AND visible = 0",
  )
    .bind(overlayDocumentId)
    .all<{ inherited_link_id: string }>();
  return new Set(results.map((row) => row.inherited_link_id));
}

interface RootRow {
  current_document_id: string;
  version: number;
  published_at: string;
}

/**
 * Resolves a `linked` server profile: orientation and Throne ownership
 * always come from the global document (they are never overridable), every
 * other identity field falls back to the global value unless the overlay
 * document has an explicit override row for it, and links are the global
 * document's enabled links (minus any the overlay explicitly hides) plus
 * the overlay's own server-local links. Nothing here is ever copied into
 * the overlay document -- the global document is read fresh on every call.
 */
async function resolveLinkedOverlay(
  env: Env,
  globalRoot: RootRow,
  serverRoot: RootRow & { id: string },
): Promise<ResolvedProfile | null> {
  const [globalDoc, overlayDoc] = await Promise.all([
    loadDocument(env, globalRoot.current_document_id),
    loadDocument(env, serverRoot.current_document_id),
  ]);
  if (globalDoc === null || overlayDoc === null) return null;

  const overridden = await loadOverriddenFields(env, overlayDoc.id);

  const [globalSelections, overlaySelections, globalAliases, overlayAliases, globalLinkRows, overlayLinkRows, hiddenIds] =
    await Promise.all([
      loadSelections(env, globalDoc.id),
      overridden.has("pronouns") || overridden.has("honourifics") || overridden.has("submissive_labels")
        ? loadSelections(env, overlayDoc.id)
        : Promise.resolve<ResolvedSelections>({ pronouns: [], honourifics: [], submissiveLabels: [] }),
      loadAliases(env, globalDoc.id),
      overridden.has("aliases") ? loadAliases(env, overlayDoc.id) : Promise.resolve<string[]>([]),
      loadLinks(env, globalDoc.id),
      loadLinks(env, overlayDoc.id),
      loadHiddenInheritedLinkIds(env, overlayDoc.id),
    ]);

  const selections: ResolvedSelections = {
    pronouns: overridden.has("pronouns") ? overlaySelections.pronouns : globalSelections.pronouns,
    honourifics: overridden.has("honourifics") ? overlaySelections.honourifics : globalSelections.honourifics,
    submissiveLabels: overridden.has("submissive_labels")
      ? overlaySelections.submissiveLabels
      : globalSelections.submissiveLabels,
  };
  const aliases = overridden.has("aliases") ? overlayAliases : globalAliases;
  const dmStatus = overridden.has("dm_status") ? overlayDoc.dm_status : globalDoc.dm_status;
  const bio = overridden.has("bio") ? overlayDoc.bio : globalDoc.bio;
  const publicSendStats = overridden.has("public_send_stats")
    ? overlayDoc.public_send_stats === 1
    : globalDoc.public_send_stats === 1;

  const inheritedLinks = globalLinkRows
    .filter((row) => !hiddenIds.has(row.id))
    .map(toResolvedLink);
  const localLinks = overlayLinkRows.map(toResolvedLink);
  const links = [...inheritedLinks, ...localLinks].sort(byOrderThenId);

  const preferredCandidate = overlayDoc.preferred_payment_link_id ?? globalDoc.preferred_payment_link_id;

  return {
    scope: "server",
    mode: "linked",
    ownerUserId: globalDoc.owner_user_id,
    // Orientation and Throne ownership are never overridable: they gate
    // capabilities (payment/Throne availability) and webhook ownership, so
    // a server overlay can never present a different orientation than the
    // owner's global identity.
    orientation: globalDoc.orientation,
    dmStatus,
    bio,
    publicSendStats,
    selections,
    aliases,
    links,
    preferredPaymentLinkId: choosePreferredPayment(links, preferredCandidate),
    throneConnected: globalDoc.throne_creator_id !== null,
    sendStats: null,
    version: serverRoot.version,
    publishedAt: serverRoot.published_at,
  };
}

interface SendStatsRow {
  currency: string;
  count: number;
  total_amount_minor: number;
}

/** Per-currency counts/totals for sends in `guildId` attributed to `ownerUserId` (see
 * `aliasAttribution.ts` for how `sender_discord_user_id` gets set). Grouped by currency with no
 * conversion, since Bill never converts between currencies anywhere else either. */
async function loadPublicSendStats(env: Env, guildId: string, ownerUserId: string): Promise<SendStatsEntry[]> {
  const { results } = await env.DB.prepare(
    `SELECT te.currency AS currency, COUNT(*) AS count, SUM(te.amount_minor) AS total_amount_minor
       FROM sends s
       JOIN throne_events te ON te.id = s.event_id
      WHERE s.guild_id = ? AND s.sender_discord_user_id = ?
      GROUP BY te.currency
      ORDER BY te.currency`,
  )
    .bind(guildId, ownerUserId)
    .all<SendStatsRow>();
  return results.map((row) => ({ currency: row.currency, count: row.count, totalAmountMinor: row.total_amount_minor }));
}

/** Attaches this guild's public send stats to an already-resolved profile, but only when the
 * owner has actually opted in (`publicSendStats`) -- every resolution branch below funnels its
 * result through this before returning, so no caller can forget the privacy check. */
async function withSendStats(env: Env, guildId: string, userId: string, profile: ResolvedProfile): Promise<ResolvedProfile> {
  if (!profile.publicSendStats) return profile;
  const sendStats = await loadPublicSendStats(env, guildId, userId);
  return { ...profile, sendStats };
}

/**
 * Resolves the profile a viewer would see for `userId` while acting in
 * `guildId`, per the fixed algorithm:
 *
 * 1. In the home guild, the global document *is* the profile.
 * 2. Elsewhere, a published `server_profiles` row is required.
 * 3. `independent` server profiles resolve entirely on their own document.
 * 4. `linked` server profiles overlay sparse overrides onto the live
 *    global document (see `resolveLinkedOverlay`).
 */
export async function resolveProfile(env: Env, guildId: string, userId: string): Promise<ProfileLookupResult> {
  const homeGuildId = requireHomeGuildId(env);

  const globalRoot = await env.DB.prepare(
    "SELECT current_document_id, version, published_at FROM global_profiles WHERE owner_user_id = ?",
  )
    .bind(userId)
    .first<RootRow>();
  const globalAvailable = globalRoot !== null;

  if (guildId === homeGuildId) {
    if (globalRoot === null) return { profile: null, globalAvailable: false };
    const document = await loadDocument(env, globalRoot.current_document_id);
    if (document === null) return { profile: null, globalAvailable: false };
    const profile = await resolveCompleteDocument(env, "global", null, document, globalRoot.version, globalRoot.published_at);
    return { profile: await withSendStats(env, guildId, userId, profile), globalAvailable: true };
  }

  const serverRoot = await env.DB.prepare(
    `SELECT id, mode, current_document_id, version, published_at
       FROM server_profiles WHERE guild_id = ? AND owner_user_id = ?`,
  )
    .bind(guildId, userId)
    .first<{ id: string; mode: "linked" | "independent"; current_document_id: string; version: number; published_at: string }>();
  if (serverRoot === null) return { profile: null, globalAvailable };

  if (serverRoot.mode === "independent") {
    const document = await loadDocument(env, serverRoot.current_document_id);
    if (document === null) return { profile: null, globalAvailable };
    const profile = await resolveCompleteDocument(
      env,
      "server",
      "independent",
      document,
      serverRoot.version,
      serverRoot.published_at,
    );
    return { profile: await withSendStats(env, guildId, userId, profile), globalAvailable };
  }

  if (globalRoot === null) return { profile: null, globalAvailable };
  const profile = await resolveLinkedOverlay(env, globalRoot, serverRoot);
  if (profile === null) return { profile: null, globalAvailable };
  return { profile: await withSendStats(env, guildId, userId, profile), globalAvailable };
}
