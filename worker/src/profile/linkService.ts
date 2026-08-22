/**
 * Manual, one-link-at-a-time CRUD for a draft's own links: add, edit, and
 * remove. This sits alongside (not instead of) the full-array `links` step
 * mutation in `draftService.ts` -- that endpoint replaces the entire link
 * list at once (what the wizard's "links" step submits), while these
 * endpoints let a caller mutate a single existing row (what "Edit an
 * existing link" or "Remove this link" in the review/edit UI needs)
 * without resubmitting every other link.
 *
 * Every mutation is the same optimistic-concurrency compare-and-swap
 * pattern used throughout the draft system: the first statement in the
 * batch is the real `UPDATE ... WHERE revision = ?old` compare-and-swap,
 * and the document rewrite statements are guarded by an `EXISTS` subquery
 * so a stale caller's request has no effect at all (see
 * `documentStore.ts`'s module docstring for why this shape is required
 * instead of throwing mid-batch).
 */
import type { Env } from "../env.js";
import { newId, nowIso } from "../util/id.js";
import { LIMITS, ORIENTATION_CAPABILITIES, validateHttpsUrl, type Orientation } from "./contracts.js";
import {
  buildDocumentWriteStatements,
  EMPTY_SNAPSHOT,
  readDocumentSnapshot,
  type DocumentLinkInput,
  type DocumentSnapshot,
} from "./documentStore.js";
import { classifyKnownProvider } from "./linkProviders.js";
import {
  badRequest,
  buildContract,
  conflict,
  countResolvedVisibleLinks,
  loadOwnedDraft,
  resolveGoverningOrientation,
  type DraftContract,
  type DraftRow,
} from "./draftService.js";

export interface LinkMutationInput {
  readonly draftId: string;
  readonly ownerUserId: string;
  readonly expectedRevision: number;
  /** Present for an edit, absent (null) when adding a new link. */
  readonly linkId: string | null;
  /** Caller-selected platform; ignored (and replaced) when the URL matches a known provider. */
  readonly platform: string | null;
  readonly publicLabel: string;
  readonly username: string | null;
  readonly normalizedUrl: string;
  /** Required when the URL's host is not a known provider; ignored (and replaced) otherwise. */
  readonly linkType: "social" | "payment" | null;
  readonly enabled: boolean;
  /** `true` to make this the preferred payment link, `false` to explicitly clear it if it
   * currently is, or `null`/omitted to leave the document's preferred link unchanged. */
  readonly preferred: boolean | null;
}

export interface RemoveLinkInput {
  readonly draftId: string;
  readonly ownerUserId: string;
  readonly expectedRevision: number;
  readonly linkId: string;
}

function requireNonEmptyString(value: string, field: string): string {
  const trimmed = value.trim();
  if (trimmed.length === 0) badRequest("invalid_field", `${field} must be a non-empty string`);
  return trimmed;
}

/**
 * Classifies a link's platform/type: a known provider hostname always wins
 * (so a caller cannot mislabel a recognized service), otherwise the caller
 * must have supplied both explicitly -- this is exactly the "known-provider
 * classification or caller-selected type" rule from manual link entry.
 */
function classifyPlatformAndType(
  normalizedUrl: string,
  callerPlatform: string | null,
  callerLinkType: "social" | "payment" | null,
): { platform: string; linkType: "social" | "payment" } {
  const known = classifyKnownProvider(normalizedUrl);
  if (known !== null) return { platform: known.platform, linkType: known.linkType };

  if (callerPlatform === null || callerPlatform.trim().length === 0) {
    badRequest("platform_required", "platform is required for links that are not a recognized provider");
  }
  if (callerLinkType === null) {
    badRequest("link_type_required", "link_type is required for links that are not a recognized provider");
  }
  return { platform: callerPlatform.trim(), linkType: callerLinkType };
}

async function loadDraftAndSnapshot(
  env: Env,
  draftId: string,
  ownerUserId: string,
  expectedRevision: number,
): Promise<{ draft: DraftRow; current: DocumentSnapshot; governingOrientation: Orientation | null }> {
  const draft = await loadOwnedDraft(env, draftId, ownerUserId);
  if (draft.status !== "active") conflict("draft_not_active", "this draft has already been published or restarted");
  if (draft.revision !== expectedRevision) {
    conflict("stale_revision", "expected_revision does not match the draft's current revision");
  }
  const current = (await readDocumentSnapshot(env, draft.document_id)) ?? EMPTY_SNAPSHOT;
  const governingOrientation = await resolveGoverningOrientation(env, draft, current);
  return { draft, current, governingOrientation };
}

/** Writes a fully-computed replacement snapshot for `draft` back to D1 under the standard
 * revision compare-and-swap, then returns the refreshed draft contract. Shared by add/edit/remove
 * so all three fail identically (409 stale_revision) when the draft moved underneath the caller. */
async function commitSnapshot(env: Env, draft: DraftRow, newSnapshot: DocumentSnapshot): Promise<DraftContract> {
  const now = nowIso();
  const newRevision = draft.revision + 1;
  const guard = { draftId: draft.id, newRevision };

  const statements: D1PreparedStatement[] = [
    env.DB.prepare("UPDATE profile_drafts SET revision = ?, updated_at = ? WHERE id = ? AND revision = ? AND status = 'active'").bind(
      newRevision,
      now,
      draft.id,
      draft.revision,
    ),
    ...buildDocumentWriteStatements(env, draft.document_id, draft.owner_user_id, newSnapshot, now, {
      isNew: false,
      guard,
    }),
  ];

  const results = await env.DB.batch(statements);
  const guardResult = results[0];
  if (guardResult === undefined || guardResult.meta.changes === 0) {
    conflict("stale_revision", "expected_revision does not match the draft's current revision");
  }

  const updated = await loadOwnedDraft(env, draft.id, draft.owner_user_id);
  return buildContract(env, updated);
}

/** Adds a new link (`input.linkId === null`) or edits an existing one, enforcing HTTPS/format
 * validation, known-provider classification, the twelve-link cap, and safe URL deduplication. */
export async function upsertDraftLink(env: Env, input: LinkMutationInput): Promise<DraftContract> {
  const { draft, current, governingOrientation } = await loadDraftAndSnapshot(
    env,
    input.draftId,
    input.ownerUserId,
    input.expectedRevision,
  );
  if (governingOrientation === null) badRequest("orientation_required", "orientation must be chosen before adding links");

  const publicLabel = requireNonEmptyString(input.publicLabel, "public_label");
  if (publicLabel.length > LIMITS.linkLabelMaxChars) {
    badRequest("link_label_too_long", `public_label must be at most ${LIMITS.linkLabelMaxChars} characters`);
  }
  if (input.normalizedUrl.length > LIMITS.linkUrlMaxChars) {
    badRequest("link_url_too_long", `normalized_url must be at most ${LIMITS.linkUrlMaxChars} characters`);
  }
  const normalizedUrl = validateHttpsUrl(input.normalizedUrl, "normalized_url");
  const { platform, linkType } = classifyPlatformAndType(normalizedUrl, input.platform, input.linkType);

  const caps = ORIENTATION_CAPABILITIES[governingOrientation];
  if (linkType === "payment" && !caps.payment) {
    badRequest("payment_links_unavailable", "this orientation does not support payment links");
  }

  const existingIndex = input.linkId === null ? -1 : current.links.findIndex((link) => link.id === input.linkId);
  if (input.linkId !== null && existingIndex === -1) {
    badRequest("unknown_link_id", "linkId must reference a link already present on this document");
  }
  const duplicate = current.links.some(
    (link, index) => index !== existingIndex && link.normalizedUrl === normalizedUrl,
  );
  if (duplicate) badRequest("duplicate_link", "normalized_url duplicates an existing link");

  const newLink: DocumentLinkInput = {
    id: input.linkId ?? newId(),
    platform,
    publicLabel,
    username: input.username,
    normalizedUrl,
    linkType,
    enabled: input.enabled,
  };

  const links = [...current.links];
  if (existingIndex === -1) {
    if (links.length >= LIMITS.linkMaxCount) {
      badRequest("too_many_links", `at most ${LIMITS.linkMaxCount} links are allowed`);
    }
    links.push(newLink);
  } else {
    links[existingIndex] = newLink;
  }

  const linked = draft.target_scope === "server" && draft.server_mode === "linked";
  if (linked) {
    const resolvedCount = await countResolvedVisibleLinks(env, draft, {
      localLinks: links,
      hiddenInheritedLinkIds: [...current.hiddenInheritedLinkIds],
    });
    if (resolvedCount > LIMITS.linkMaxCount) {
      badRequest("too_many_links", `at most ${LIMITS.linkMaxCount} resolved links are allowed`);
    }
  }

  let preferredPaymentLinkId = current.preferredPaymentLinkId;
  if (input.preferred === true) {
    if (linkType !== "payment") badRequest("preferred_requires_payment", "only a payment link can be preferred");
    preferredPaymentLinkId = newLink.id;
  } else if (input.preferred === false && preferredPaymentLinkId === input.linkId) {
    preferredPaymentLinkId = null;
  }

  const newSnapshot: DocumentSnapshot = { ...current, links, preferredPaymentLinkId };
  return commitSnapshot(env, draft, newSnapshot);
}

/** Removes an existing link, clearing `preferred_payment_link_id` first if it pointed at the
 * removed link (a dangling preference would otherwise silently resolve to nothing later). */
export async function removeDraftLink(env: Env, input: RemoveLinkInput): Promise<DraftContract> {
  const { draft, current } = await loadDraftAndSnapshot(env, input.draftId, input.ownerUserId, input.expectedRevision);

  const existingIndex = current.links.findIndex((link) => link.id === input.linkId);
  if (existingIndex === -1) badRequest("unknown_link_id", "linkId must reference a link already present on this document");

  const links = current.links.filter((link) => link.id !== input.linkId);
  const preferredPaymentLinkId = current.preferredPaymentLinkId === input.linkId ? null : current.preferredPaymentLinkId;

  const newSnapshot: DocumentSnapshot = { ...current, links, preferredPaymentLinkId };
  return commitSnapshot(env, draft, newSnapshot);
}
