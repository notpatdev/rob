/**
 * Typed contracts and validation for Bill's profile system.
 *
 * Everything a caller can send into a draft step, and everything the
 * resolver hands back out, is validated/shaped in this module so that the
 * route handlers never juggle raw `unknown` JSON bodies themselves. This
 * keeps the fixed vocabularies (orientations, pronouns, honourifics, DM
 * status) and the size/format limits in exactly one place.
 */

export const ORIENTATIONS = ["domme", "submissive", "switch_domme", "switch_submissive"] as const;
export type Orientation = (typeof ORIENTATIONS)[number];

export const DM_STATUSES = ["open", "by_request", "after_tribute", "closed"] as const;
export type DmStatus = (typeof DM_STATUSES)[number];

export const PRONOUNS = [
  "She/Her",
  "He/Him",
  "They/Them",
  "It/Its",
  "She/They",
  "He/They",
  "Any Pronouns",
  "Ask Me",
] as const;

export const HONOURIFICS = [
  "Goddess",
  "Mistress",
  "Princess",
  "Temptress",
  "Enchantress",
  "Mommy",
  "Master",
  "Daddy",
  "CashMaster",
] as const;

export const SUBMISSIVE_LABELS = [
  "Submissive",
  "Sub",
  "Brat",
  "Pet",
  "Good boy",
  "Good girl",
  "Good pet",
  "Toy",
] as const;

export const LIMITS = {
  bioMaxChars: 300,
  aliasMaxChars: 64,
  aliasMaxCount: 3,
  linkLabelMaxChars: 40,
  linkUrlMaxChars: 500,
  linkMaxCount: 12,
} as const;

/**
 * Per-orientation feature capabilities. Pronouns are available to every
 * orientation; everything else is gated so the wizard (and this Worker's
 * validation) never accepts fields that orientation is not entitled to.
 * Only "switch" orientations get both label collections at once; only
 * orientations that can receive tribute get Throne/payment; only
 * orientations whose sends get attributed get aliases/stats.
 */
export interface OrientationCapabilities {
  readonly honourifics: boolean;
  readonly submissiveLabels: boolean;
  readonly aliases: boolean;
  readonly stats: boolean;
  readonly throne: boolean;
  readonly payment: boolean;
}

export const ORIENTATION_CAPABILITIES: Readonly<Record<Orientation, OrientationCapabilities>> = {
  domme: {
    honourifics: true,
    submissiveLabels: false,
    aliases: false,
    stats: false,
    throne: true,
    payment: true,
  },
  submissive: {
    honourifics: false,
    submissiveLabels: true,
    aliases: true,
    stats: true,
    throne: false,
    payment: false,
  },
  switch_domme: {
    honourifics: true,
    submissiveLabels: true,
    aliases: true,
    stats: true,
    throne: true,
    payment: true,
  },
  switch_submissive: {
    honourifics: true,
    submissiveLabels: true,
    aliases: true,
    stats: true,
    throne: true,
    payment: true,
  },
};

export const STEP_KEYS = ["orientation", "identity", "links", "throne", "review"] as const;
export type StepKey = (typeof STEP_KEYS)[number];

/** The step sequence a draft must complete, given its (possibly still-unset) orientation. */
export function stepsForOrientation(orientation: Orientation | null): readonly StepKey[] {
  if (orientation === null) return ["orientation"];
  const caps = ORIENTATION_CAPABILITIES[orientation];
  return STEP_KEYS.filter((step) => step !== "throne" || caps.throne);
}

/**
 * Fields a `linked` server overlay may deliberately override. Orientation
 * and Throne ownership are excluded on purpose: they gate capabilities and
 * webhook ownership and are always inherited from the owner's global
 * identity, never chosen per-guild.
 */
export const OVERRIDABLE_FIELDS = [
  "pronouns",
  "honourifics",
  "submissive_labels",
  "dm_status",
  "bio",
  "public_send_stats",
  "aliases",
] as const;
export type OverridableField = (typeof OVERRIDABLE_FIELDS)[number];

/**
 * The step sequence for a draft, accounting for target scope/mode.
 *
 * A `linked` server draft never has its own `orientation` or `throne`
 * steps: both are read live from the owner's global document (see the
 * resolver), so there is nothing to choose. Its `identity`/`links` steps
 * instead ask which fields to override for this guild, not what the values
 * should be from scratch.
 */
export function stepsForDraft(targetScope: TargetScope, serverMode: ServerMode | null, orientation: Orientation | null): readonly StepKey[] {
  if (targetScope === "server" && serverMode === "linked") {
    return ["identity", "links", "review"];
  }
  return stepsForOrientation(orientation);
}

export class ValidationError extends Error {
  readonly code: string;
  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

function fail(code: string, message: string): never {
  throw new ValidationError(code, message);
}

export function isOrientation(value: unknown): value is Orientation {
  return typeof value === "string" && (ORIENTATIONS as readonly string[]).includes(value);
}

export function isDmStatus(value: unknown): value is DmStatus {
  return typeof value === "string" && (DM_STATUSES as readonly string[]).includes(value);
}

/** Validates a step's target scope/mode combination up front (used by draft start). */
export type TargetScope = "global" | "server";
export type ServerMode = "linked" | "independent";

export interface OrientationStepInput {
  readonly orientation: Orientation;
}

export function parseOrientationStep(body: unknown): OrientationStepInput {
  const record = asRecord(body, "orientation step body");
  if (!isOrientation(record.orientation)) {
    fail("invalid_orientation", `orientation must be one of: ${ORIENTATIONS.join(", ")}`);
  }
  return { orientation: record.orientation };
}

export interface IdentityStepInput {
  readonly pronouns: string[];
  readonly honourifics: string[];
  readonly submissiveLabels: string[];
  readonly dmStatus: DmStatus | null;
  readonly bio: string | null;
  readonly publicSendStats: boolean;
  readonly aliases: string[];
}

/**
 * Validates the identity step against the draft's already-chosen
 * orientation so a domme profile can never smuggle in submissive labels
 * (and vice versa), and so aliases/stats are only accepted where the
 * orientation supports them.
 */
export function parseIdentityStep(
  body: unknown,
  orientation: Orientation,
  allowUnselectedDmStatus = false,
): IdentityStepInput {
  const record = asRecord(body, "identity step body");
  const caps = ORIENTATION_CAPABILITIES[orientation];

  const pronouns = parseFixedMultiSelect(record.pronouns, PRONOUNS, "pronouns");

  const honourifics = caps.honourifics
    ? parseFixedMultiSelect(record.honourifics, HONOURIFICS, "honourifics")
    : (requireEmptyOrAbsent(record.honourifics, "honourifics", orientation), []);

  const submissiveLabels = caps.submissiveLabels
    ? parseFixedMultiSelect(record.submissive_labels, SUBMISSIVE_LABELS, "submissive_labels")
    : (requireEmptyOrAbsent(record.submissive_labels, "submissive_labels", orientation), []);

  if (record.dm_status !== null && !isDmStatus(record.dm_status)) {
    fail("invalid_dm_status", `dm_status must be one of: ${DM_STATUSES.join(", ")}`);
  }
  if (record.dm_status === null && !allowUnselectedDmStatus) {
    fail("dm_status_required", "dm_status must be chosen before completing identity");
  }

  const bio = parseOptionalBio(record.bio);

  const publicSendStats = caps.stats
    ? parseBooleanWithDefault(record.public_send_stats, "public_send_stats", false)
    : (requireEmptyOrAbsent(record.public_send_stats, "public_send_stats", orientation), false);

  const aliases = caps.aliases
    ? parseAliases(record.aliases)
    : (requireEmptyOrAbsent(record.aliases, "aliases", orientation), []);

  return {
    pronouns,
    honourifics,
    submissiveLabels,
    dmStatus: record.dm_status as DmStatus | null,
    bio,
    publicSendStats,
    aliases,
  };
}

export interface LinkStepInputLink {
  /** Echoed back from a prior read to keep an existing link's id stable; omitted/absent for a new link. */
  readonly id: string | null;
  readonly platform: string;
  readonly publicLabel: string;
  readonly username: string | null;
  readonly normalizedUrl: string;
  readonly linkType: "social" | "payment";
  readonly enabled: boolean;
}

export interface LinkStepInput {
  readonly links: LinkStepInputLink[];
}

function parseLinkArray(raw: unknown, caps: OrientationCapabilities, fieldPrefix: string): LinkStepInputLink[] {
  if (!Array.isArray(raw)) fail("invalid_links", `${fieldPrefix} must be an array`);
  if (raw.length > LIMITS.linkMaxCount) {
    fail("too_many_links", `at most ${LIMITS.linkMaxCount} ${fieldPrefix} are allowed`);
  }

  const seenUrls = new Set<string>();
  const seenIds = new Set<string>();
  return raw.map((entry, index) => {
    const linkRecord = asRecord(entry, `${fieldPrefix}[${index}]`);

    const id = linkRecord.id === undefined || linkRecord.id === null
      ? null
      : requireNonEmptyString(linkRecord.id, `${fieldPrefix}[${index}].id`);
    if (id !== null) {
      if (seenIds.has(id)) fail("duplicate_link", `${fieldPrefix}[${index}].id duplicates an earlier link`);
      seenIds.add(id);
    }

    const platform = requireNonEmptyString(linkRecord.platform, `${fieldPrefix}[${index}].platform`);

    const publicLabel = requireNonEmptyString(linkRecord.public_label, `${fieldPrefix}[${index}].public_label`);
    if (publicLabel.length > LIMITS.linkLabelMaxChars) {
      fail(
        "link_label_too_long",
        `${fieldPrefix}[${index}].public_label must be at most ${LIMITS.linkLabelMaxChars} characters`,
      );
    }

    const username = linkRecord.username === undefined || linkRecord.username === null
      ? null
      : requireNonEmptyString(linkRecord.username, `${fieldPrefix}[${index}].username`);

    const url = requireNonEmptyString(linkRecord.normalized_url, `${fieldPrefix}[${index}].normalized_url`);
    if (url.length > LIMITS.linkUrlMaxChars) {
      fail(
        "link_url_too_long",
        `${fieldPrefix}[${index}].normalized_url must be at most ${LIMITS.linkUrlMaxChars} characters`,
      );
    }
    const normalizedUrl = validateHttpsUrl(url, `${fieldPrefix}[${index}].normalized_url`);
    if (seenUrls.has(normalizedUrl)) {
      fail("duplicate_link", `${fieldPrefix}[${index}].normalized_url duplicates an earlier link`);
    }
    seenUrls.add(normalizedUrl);

    const linkType = linkRecord.link_type;
    if (linkType !== "social" && linkType !== "payment") {
      fail("invalid_link_type", `${fieldPrefix}[${index}].link_type must be "social" or "payment"`);
    }
    if (linkType === "payment" && !caps.payment) {
      fail("payment_links_unavailable", "this orientation does not support payment links");
    }

    const enabled =
      linkRecord.enabled === undefined ? true : parseOptionalBoolean(linkRecord.enabled, `${fieldPrefix}[${index}].enabled`);

    return { id, platform, publicLabel, username, normalizedUrl, linkType, enabled };
  });
}

/**
 * Validates the manually-entered links step for a `global`/`independent`
 * draft (a complete replacement of the document's link list). This only
 * accepts links the caller already typed in (label/username/URL); fetching
 * a link page and scraping candidates is the separate, not-yet-implemented
 * importer.
 */
export function parseLinkStep(body: unknown, orientation: Orientation): LinkStepInput {
  const record = asRecord(body, "links step body");
  const caps = ORIENTATION_CAPABILITIES[orientation];
  const links = parseLinkArray(record.links, caps, "links");
  return { links };
}

export interface ThroneStepInput {
  readonly throneCreatorId: string | null;
  readonly preferredPaymentLinkId: string | null;
}

export function parseThroneStep(body: unknown, orientation: Orientation): ThroneStepInput {
  const caps = ORIENTATION_CAPABILITIES[orientation];
  if (!caps.throne) fail("throne_unavailable", "this orientation does not have a Throne step");
  const record = asRecord(body, "throne step body");
  const throneCreatorId = parseOptionalId(record.throne_creator_id, "throne_creator_id");
  const preferredPaymentLinkId = parseOptionalId(record.preferred_payment_link_id, "preferred_payment_link_id");
  return { throneCreatorId, preferredPaymentLinkId };
}

// --- linked server overlay steps -------------------------------------------------------------
//
// A linked draft never chooses orientation or Throne ownership (both are
// read live from the global document), and its identity/links steps ask
// "which fields should this guild override" rather than "what are the
// values from scratch". `overriddenFields`/`overrides` lists are the
// caller's complete, explicit statement of intent for this submission: a
// field left out of the list means "inherit from global", including when a
// value happens to be present elsewhere in the body (which is ignored).

export interface LinkedIdentityStepInput {
  readonly overriddenFields: ReadonlySet<OverridableField>;
  readonly pronouns: string[];
  readonly honourifics: string[];
  readonly submissiveLabels: string[];
  readonly dmStatus: DmStatus | null;
  readonly bio: string | null;
  readonly publicSendStats: boolean;
  readonly aliases: string[];
}

function parseOverridesList(value: unknown, caps: OrientationCapabilities): Set<OverridableField> {
  if (!Array.isArray(value)) fail("invalid_overrides", "overrides must be an array of field names");
  const overridden = new Set<OverridableField>();
  for (const entry of value) {
    if (typeof entry !== "string" || !(OVERRIDABLE_FIELDS as readonly string[]).includes(entry)) {
      fail("invalid_overrides", "overrides contains an unrecognized field name");
    }
    const field = entry as OverridableField;
    if (field === "honourifics" && !caps.honourifics) {
      fail("field_not_available", "honourifics cannot be overridden for this orientation");
    }
    if (field === "submissive_labels" && !caps.submissiveLabels) {
      fail("field_not_available", "submissive_labels cannot be overridden for this orientation");
    }
    if ((field === "aliases" || field === "public_send_stats") && !caps.aliases) {
      fail("field_not_available", `${field} cannot be overridden for this orientation`);
    }
    overridden.add(field);
  }
  return overridden;
}

/**
 * Validates a linked overlay's identity-step submission against the
 * owner's *live* global orientation (never the draft's own, since a linked
 * draft has no orientation of its own).
 */
export function parseLinkedIdentityStep(body: unknown, globalOrientation: Orientation): LinkedIdentityStepInput {
  const record = asRecord(body, "identity step body");
  const caps = ORIENTATION_CAPABILITIES[globalOrientation];
  const overriddenFields = parseOverridesList(record.overrides, caps);

  const pronouns = overriddenFields.has("pronouns") ? parseFixedMultiSelect(record.pronouns, PRONOUNS, "pronouns") : [];
  const honourifics = overriddenFields.has("honourifics")
    ? parseFixedMultiSelect(record.honourifics, HONOURIFICS, "honourifics")
    : [];
  const submissiveLabels = overriddenFields.has("submissive_labels")
    ? parseFixedMultiSelect(record.submissive_labels, SUBMISSIVE_LABELS, "submissive_labels")
    : [];
  const dmStatus = overriddenFields.has("dm_status")
    ? (isDmStatus(record.dm_status) ? record.dm_status : fail("invalid_dm_status", `dm_status must be one of: ${DM_STATUSES.join(", ")}`))
    : null;
  const bio = overriddenFields.has("bio") ? parseOptionalBio(record.bio) : null;
  const publicSendStats = overriddenFields.has("public_send_stats")
    ? parseOptionalBoolean(record.public_send_stats, "public_send_stats")
    : false;
  const aliases = overriddenFields.has("aliases") ? parseAliases(record.aliases) : [];

  return { overriddenFields, pronouns, honourifics, submissiveLabels, dmStatus, bio, publicSendStats, aliases };
}

export interface LinkedLinksStepInput {
  readonly localLinks: LinkStepInputLink[];
  readonly hiddenInheritedLinkIds: string[];
  readonly preferredPaymentLinkId: string | null;
}

/**
 * Validates a linked overlay's links-step submission: server-local
 * additions, which inherited global links to hide, and which currently
 * resolvable payment link is preferred in this guild. This never touches
 * the global document's own link rows.
 */
export function parseLinkedLinksStep(body: unknown, globalOrientation: Orientation): LinkedLinksStepInput {
  const record = asRecord(body, "links step body");
  const caps = ORIENTATION_CAPABILITIES[globalOrientation];
  const localLinks = parseLinkArray(record.local_links ?? [], caps, "local_links");

  const hiddenRaw = record.hidden_inherited_link_ids ?? [];
  if (!Array.isArray(hiddenRaw)) fail("invalid_field", "hidden_inherited_link_ids must be an array");
  const hiddenInheritedLinkIds = hiddenRaw.map((entry, index) =>
    requireNonEmptyString(entry, `hidden_inherited_link_ids[${index}]`),
  );

  const preferredPaymentLinkId = parseOptionalId(record.preferred_payment_link_id, "preferred_payment_link_id");

  return { localLinks, hiddenInheritedLinkIds, preferredPaymentLinkId };
}

// --- shared primitive parsing helpers -------------------------------------------------------

function asRecord(value: unknown, what: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    fail("invalid_body", `${what} must be a JSON object`);
  }
  return value as Record<string, unknown>;
}

function requireNonEmptyString(value: unknown, field: string): string {
  if (typeof value !== "string" || value.trim().length === 0) {
    fail("invalid_field", `${field} must be a non-empty string`);
  }
  return value;
}

function parseOptionalId(value: unknown, field: string): string | null {
  if (value === undefined || value === null) return null;
  if (typeof value !== "string" || value.trim().length === 0) {
    fail("invalid_field", `${field} must be a non-empty string or null`);
  }
  return value;
}

function parseOptionalBoolean(value: unknown, field: string): boolean {
  if (typeof value !== "boolean") fail("invalid_field", `${field} must be a boolean`);
  return value;
}

/** Like `parseOptionalBoolean`, but a fully-absent value falls back to `defaultValue` instead of failing. */
function parseBooleanWithDefault(value: unknown, field: string, defaultValue: boolean): boolean {
  if (value === undefined) return defaultValue;
  return parseOptionalBoolean(value, field);
}

function requireEmptyOrAbsent(value: unknown, field: string, orientation: Orientation): void {
  const isEmpty =
    value === undefined ||
    value === null ||
    (Array.isArray(value) && value.length === 0) ||
    value === false;
  if (!isEmpty) {
    fail("field_not_available", `${field} is not available for orientation ${orientation}`);
  }
}

function parseFixedMultiSelect(value: unknown, allowed: readonly string[], field: string): string[] {
  if (value === undefined) return [];
  if (!Array.isArray(value)) fail("invalid_field", `${field} must be an array`);
  const result: string[] = [];
  const seen = new Set<string>();
  for (const entry of value) {
    if (typeof entry !== "string" || !allowed.includes(entry)) {
      fail("invalid_field_value", `${field} contains an unrecognized value`);
    }
    if (!seen.has(entry)) {
      seen.add(entry);
      result.push(entry);
    }
  }
  return result;
}

function parseOptionalBio(value: unknown): string | null {
  if (value === undefined || value === null) return null;
  if (typeof value !== "string") fail("invalid_field", "bio must be a string or null");
  const trimmed = value.trim();
  if (trimmed.length === 0) return null;
  if (trimmed.length > LIMITS.bioMaxChars) {
    fail("bio_too_long", `bio must be at most ${LIMITS.bioMaxChars} characters`);
  }
  return trimmed;
}

/**
 * Normalizes an alias for storage: Unicode NFKC normalization, case
 * folding, a stripped leading "@", and whitespace collapse. This is what
 * makes "@Foo_Bar", "foo_bar", and "  foo_bar  " collide as duplicates and
 * what future webhook attribution matches sender names against.
 */
export function normalizeAlias(displayAlias: string): string {
  return displayAlias
    .normalize("NFKC")
    .trim()
    .replace(/^@+/, "")
    .replace(/\s+/g, " ")
    .toLowerCase();
}

function parseAliases(value: unknown): string[] {
  if (value === undefined) return [];
  if (!Array.isArray(value)) fail("invalid_field", "aliases must be an array");
  if (value.length > LIMITS.aliasMaxCount) {
    fail("too_many_aliases", `at most ${LIMITS.aliasMaxCount} aliases are allowed`);
  }
  const seenNormalized = new Set<string>();
  const aliases: string[] = [];
  for (const entry of value) {
    if (typeof entry !== "string") fail("invalid_field", "each alias must be a string");
    const trimmed = entry.trim();
    if (trimmed.length === 0) fail("invalid_alias", "aliases must not be empty");
    if (trimmed.length > LIMITS.aliasMaxChars) {
      fail("alias_too_long", `each alias must be at most ${LIMITS.aliasMaxChars} characters`);
    }
    const normalized = normalizeAlias(trimmed);
    if (normalized.length === 0) fail("invalid_alias", "aliases must contain visible characters");
    if (seenNormalized.has(normalized)) {
      fail("duplicate_alias", "aliases must be unique once normalized");
    }
    seenNormalized.add(normalized);
    aliases.push(trimmed);
  }
  return aliases;
}

/**
 * Format-only URL validation for manually-entered links: HTTPS scheme, no
 * embedded credentials, and a length cap. This is deliberately *not* the
 * SSRF-hardened fetch/DNS policy the (not-yet-implemented) link importer
 * will need, because these URLs are only ever stored and rendered as
 * Discord link buttons, never fetched by the Worker.
 */
export function validateHttpsUrl(value: string, field: string): string {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    fail("invalid_url", `${field} must be a well-formed URL`);
  }
  if (parsed.protocol !== "https:") {
    fail("invalid_url_scheme", `${field} must use https`);
  }
  if (parsed.username.length > 0 || parsed.password.length > 0) {
    fail("invalid_url_credentials", `${field} must not contain embedded credentials`);
  }
  return parsed.toString();
}
