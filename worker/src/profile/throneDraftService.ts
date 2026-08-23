/**
 * Throne connection for a profile draft: resolving/attaching a Throne
 * creator (new or already-owned) to the draft's document, and explicit
 * webhook secret rotation. This is the draft-scoped counterpart to
 * `throne/creatorService.ts`'s guild-scoped `/register` flow -- both share
 * the same resolution/secret-issuance code, but only this module writes
 * the connection into a profile document rather than a guild registration.
 */
import type { Env } from "../env.js";
import { newRouteSecret, nowIso } from "../util/id.js";
import { constantTimeEqualHex, sha256Hex } from "../util/hash.js";
import { ORIENTATION_CAPABILITIES, type Orientation } from "./contracts.js";
import { EMPTY_SNAPSHOT, buildDocumentWriteStatements, readDocumentSnapshot, type DocumentSnapshot } from "./documentStore.js";
import {
  badRequest,
  buildContract,
  conflict,
  loadOwnedDraft,
  resolveGoverningOrientation,
  type DraftContract,
  type DraftRow,
} from "./draftService.js";
import {
  buildPreparedCreatorStatements,
  findCreatorByPublicId,
  httpStatusForThroneErrorCode,
  prepareAttachmentForIdentity,
  prepareThroneCreatorAttachment,
  prepareWebhookSecret,
  resolveThroneIdentity,
  ThroneResolutionError,
  type ResolvedThroneIdentity,
  type SqlMutationGuard,
  type WebhookState,
} from "../throne/creatorService.js";
import { DraftError } from "./draftService.js";

/** How long a resolved-but-unconfirmed Throne identity stays confirmable. Long enough for a
 * member to read the handle and press a button, short enough that an abandoned confirmation
 * cannot be replayed later against a handle that has since changed hands on Throne. */
const PENDING_THRONE_TTL_MS = 15 * 60 * 1000;

const CLEAR_PENDING_THRONE_SQL = `,
              pending_throne_token_hash = NULL, pending_throne_public_creator_id = NULL,
              pending_throne_handle = NULL, pending_throne_profile_url = NULL,
              pending_throne_expires_at = NULL`;

/** Re-raises a Throne resolution failure with the same HTTP status the legacy `/register` route
 * uses, so "not found" is a 404 and "already linked by someone else" a 409 here too. */
function failThroneResolution(error: ThroneResolutionError): never {
  throw new DraftError(httpStatusForThroneErrorCode(error.code), error.code, error.message);
}

function requireThroneCapableDraft(draft: DraftRow, governingOrientation: Orientation | null): void {
  if (governingOrientation === null || !ORIENTATION_CAPABILITIES[governingOrientation].throne) {
    badRequest("throne_unavailable", "this orientation does not have a Throne step");
  }
  if (draft.target_scope === "server" && draft.server_mode === "linked") {
    // Throne ownership is never overridable per-guild: a linked overlay always inherits the
    // owner's global connection (see resolver.ts), so there is nothing to attach here.
    badRequest("step_not_applicable", "a linked server profile inherits Throne connection from the global profile");
  }
}

async function loadMutableDraft(
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

function draftMutationGuard(draft: DraftRow): SqlMutationGuard {
  return {
    sql: `EXISTS (
      SELECT 1
        FROM profile_drafts d
        JOIN profile_documents p ON p.id = d.document_id
       WHERE d.id = ? AND d.revision = ? AND d.status = 'active'
         AND d.document_id = ? AND p.state = 'draft'
    )`,
    params: [draft.id, draft.revision, draft.document_id],
  };
}

function isConstraintError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  return /constraint failed/i.test(message);
}

/** Bumps the draft's revision under the standard compare-and-swap and returns the refreshed
 * contract; used both when the document actually changes (attach) and when it does not
 * (rotate-only), so every Throne mutation advances the same optimistic-concurrency counter. */
async function bumpRevisionAndReturn(
  env: Env,
  draft: DraftRow,
  newSnapshot: DocumentSnapshot | null,
  prefixStatements: readonly D1PreparedStatement[] = [],
  options: { clearPendingThrone?: boolean } = {},
): Promise<DraftContract> {
  const now = nowIso();
  const newRevision = draft.revision + 1;
  const guard = { draftId: draft.id, expectedRevision: draft.revision };

  const statements: D1PreparedStatement[] = [...prefixStatements];
  if (newSnapshot !== null) {
    statements.push(...buildDocumentWriteStatements(env, draft.document_id, draft.owner_user_id, newSnapshot, now, { isNew: false, guard }));
  }

  statements.push(
    env.DB.prepare(
      `UPDATE profile_drafts
          SET revision = ?, updated_at = ?${options.clearPendingThrone === true ? CLEAR_PENDING_THRONE_SQL : ""}
        WHERE id = ? AND revision = ? AND status = 'active'
          AND EXISTS (SELECT 1 FROM profile_documents WHERE id = ? AND state = 'draft')`,
    ).bind(newRevision, now, draft.id, draft.revision, draft.document_id),
  );

  let results: D1Result[];
  try {
    results = await env.DB.batch(statements);
  } catch (error) {
    if (!isConstraintError(error)) throw error;
    conflict("throne_attach_conflict", "the Throne connection changed; reload the draft and try again");
  }
  const guardResult = results.at(-1);
  if (guardResult === undefined || guardResult.meta.changes === 0) {
    conflict("stale_revision", "expected_revision does not match the draft's current revision");
  }

  const updated = await loadOwnedDraft(env, draft.id, draft.owner_user_id);
  return buildContract(env, updated);
}

export interface ResolveThroneInput {
  readonly draftId: string;
  readonly ownerUserId: string;
  readonly expectedRevision: number;
  readonly throneInput: string;
}

export interface ThroneResolveResult {
  readonly draft: DraftContract;
  /** The handle the owner is being asked to confirm. */
  readonly handle: string;
  /** True when this Throne creator is already linked to this same Discord user *and* has
   * already proven its webhook by delivering a signed Throne payload -- so confirming can reuse
   * the existing connection instead of walking the owner through webhook setup again. */
  readonly alreadyVerified: boolean;
  /** One-time, opaque capability authorizing exactly one confirmation of exactly this
   * resolution on exactly this draft. Returned once; only its hash is stored. */
  readonly confirmationToken: string;
  readonly expiresAt: string;
}

/**
 * Step one of the two-step Throne connection: resolve the owner's submitted
 * username/URL and stage what was found, *without* creating a
 * `throne_creators` row, minting a webhook secret, or touching the draft's
 * document.
 *
 * Nothing here is success-shaped. The response deliberately carries no
 * creator id (neither Bill's nor Throne's), no secret, and no webhook URL:
 * only the handle to show on the "is this you?" screen, whether that
 * creator is already verified for this same user, and an opaque capability
 * to confirm with. Because the staged identity lives on the draft row, a
 * confirmation screen survives a bot restart, and an abandoned resolution
 * simply expires -- it can never leave a live webhook route or an orphan
 * creator row behind.
 */
export async function resolveThroneForDraft(env: Env, input: ResolveThroneInput): Promise<ThroneResolveResult> {
  const { draft, governingOrientation } = await loadMutableDraft(
    env,
    input.draftId,
    input.ownerUserId,
    input.expectedRevision,
  );
  requireThroneCapableDraft(draft, governingOrientation);

  let identity: ResolvedThroneIdentity;
  try {
    identity = await resolveThroneIdentity(input.throneInput);
  } catch (error) {
    if (error instanceof ThroneResolutionError) failThroneResolution(error);
    throw error;
  }

  // Ownership is checked here as well as at confirmation time, so a member who
  // typed somebody else's handle is told immediately rather than after agreeing
  // to connect it -- and either way no row or secret is created for it.
  const existing = await findCreatorByPublicId(env, identity.publicCreatorId);
  if (existing !== null && existing.owner_discord_user_id !== draft.owner_user_id) {
    throw new DraftError(409, "creator_owned", "That Throne creator is already linked by a different Discord user");
  }
  const alreadyVerified = existing !== null && (existing.webhook_verified_at ?? null) !== null;

  const confirmationToken = newRouteSecret();
  const tokenHash = await sha256Hex(confirmationToken);
  const now = nowIso();
  const expiresAt = new Date(Date.now() + PENDING_THRONE_TTL_MS).toISOString();
  const newRevision = draft.revision + 1;
  const confirmationSubstep =
    draft.wizard_substep === "review" || draft.wizard_substep?.startsWith("review:")
      ? "review:confirm"
      : "confirm";

  const results = await env.DB.batch([
    env.DB.prepare(
      `UPDATE profile_drafts
          SET pending_throne_token_hash = ?, pending_throne_public_creator_id = ?,
              pending_throne_handle = ?, pending_throne_profile_url = ?,
              pending_throne_expires_at = ?, wizard_stage = 'throne',
              wizard_substep = ?, revision = ?, updated_at = ?
        WHERE id = ? AND revision = ? AND status = 'active'`,
    ).bind(
      tokenHash,
      identity.publicCreatorId,
      identity.handle,
      identity.profileUrl,
      expiresAt,
      confirmationSubstep,
      newRevision,
      now,
      draft.id,
      draft.revision,
    ),
  ]);
  const guardResult = results.at(-1);
  if (guardResult === undefined || guardResult.meta.changes === 0) {
    conflict("stale_revision", "expected_revision does not match the draft's current revision");
  }

  const updated = await loadOwnedDraft(env, draft.id, draft.owner_user_id);
  return {
    draft: await buildContract(env, updated),
    handle: identity.handle,
    alreadyVerified,
    confirmationToken,
    expiresAt,
  };
}

/** Validates a confirmation capability against the identity staged on this draft, returning that
 * identity. Rejects a missing, mismatched, or expired staging without revealing which. */
async function consumePendingIdentity(draft: DraftRow, confirmationToken: string): Promise<ResolvedThroneIdentity> {
  const storedHash = draft.pending_throne_token_hash;
  const publicCreatorId = draft.pending_throne_public_creator_id;
  const handle = draft.pending_throne_handle;
  const profileUrl = draft.pending_throne_profile_url;
  if (storedHash === null || publicCreatorId === null || handle === null || profileUrl === null) {
    badRequest("invalid_confirmation_token", "this draft has no Throne connection awaiting confirmation");
  }
  const presentedHash = await sha256Hex(confirmationToken);
  if (!(await constantTimeEqualHex(presentedHash, storedHash))) {
    badRequest("invalid_confirmation_token", "that Throne confirmation is no longer valid; resolve the handle again");
  }
  const expiresAt = draft.pending_throne_expires_at;
  if (expiresAt !== null && Date.parse(expiresAt) <= Date.now()) {
    badRequest("throne_confirmation_expired", "that Throne confirmation expired; resolve the handle again");
  }
  return { publicCreatorId, handle, profileUrl };
}

export interface AttachThroneInput {
  readonly draftId: string;
  readonly ownerUserId: string;
  readonly expectedRevision: number;
  /** A Throne username/profile URL to resolve and attach in one step. Mutually exclusive with
   * the other two inputs, and never used by the confirmed wizard flow. */
  readonly throneInput: string | null;
  /** An id from this draft's `thronePrefill.ownedCreators`, to reattach a creator already owned
   * by this user without re-resolving it against Throne. */
  readonly existingCreatorId: string | null;
  /** The capability returned by `resolveThroneForDraft`: confirms the exact handle the owner was
   * shown, with no creator id ever leaving the Worker. */
  readonly confirmationToken: string | null;
  /** Confirms the currently staged identity using the bearer-authenticated, owner/revision-bound
   * draft capability. This is the restart-safe wizard path: the plaintext one-time token is not
   * persisted or embedded in Discord custom IDs. */
  readonly confirmPending?: boolean;
  readonly rotateWebhook: boolean;
}

export interface ThroneDraftResult {
  readonly draft: DraftContract;
  readonly webhookUrl: string | null;
  readonly webhookState: WebhookState | "unchanged";
}

/**
 * Attaches a Throne creator to this draft's document. Exactly one of
 * `confirmationToken` (the wizard's confirmed flow), `existingCreatorId`
 * (reattaching a creator this user already owns), or `throneInput` (direct,
 * unconfirmed resolution) must be supplied. This is the only place a webhook
 * secret is ever minted for a draft, and its plaintext URL is returned exactly
 * once, on the request that issues or rotates it.
 */
export async function attachThroneToDraft(env: Env, input: AttachThroneInput): Promise<ThroneDraftResult> {
  const { draft, current, governingOrientation } = await loadMutableDraft(
    env,
    input.draftId,
    input.ownerUserId,
    input.expectedRevision,
  );
  requireThroneCapableDraft(draft, governingOrientation);

  const supplied = [
    input.throneInput,
    input.existingCreatorId,
    input.confirmationToken,
    input.confirmPending ? "pending" : null,
  ].filter((value) => value !== null);
  if (supplied.length !== 1) {
    badRequest(
      "throne_input_required",
      "exactly one Throne confirmation or attachment input is required",
    );
  }

  let creatorId: string;
  let webhookUrl: string | null = null;
  let webhookState: WebhookState | "unchanged" = "unchanged";
  let creatorStatements: D1PreparedStatement[] = [];
  let clearPendingThrone = false;
  const mutationGuard = draftMutationGuard(draft);

  if (input.confirmationToken !== null || input.confirmPending) {
    const identity =
      input.confirmationToken !== null
        ? await consumePendingIdentity(draft, input.confirmationToken)
        : pendingIdentityForOwnedDraft(draft);
    let prepared;
    try {
      // The staged identity, not a fresh network lookup, is what gets attached: the
      // owner confirmed *that* handle, so a Throne-side rename between the two
      // requests can never silently connect a different creator.
      prepared = await prepareAttachmentForIdentity(env, draft.owner_user_id, identity, {
        rotateWebhook: input.rotateWebhook,
      });
    } catch (error) {
      if (error instanceof ThroneResolutionError) failThroneResolution(error);
      throw error;
    }

    function pendingIdentityForOwnedDraft(draft: DraftRow): ResolvedThroneIdentity {
      const publicCreatorId = draft.pending_throne_public_creator_id;
      const handle = draft.pending_throne_handle;
      const profileUrl = draft.pending_throne_profile_url;
      if (publicCreatorId === null || handle === null || profileUrl === null) {
        badRequest("pending_throne_required", "this draft has no Throne connection awaiting confirmation");
      }
      const expiresAt = draft.pending_throne_expires_at;
      if (expiresAt !== null && Date.parse(expiresAt) <= Date.now()) {
        badRequest("throne_confirmation_expired", "that Throne confirmation expired; resolve the handle again");
      }
      return { publicCreatorId, handle, profileUrl };
    }
    creatorId = prepared.creatorId;
    webhookUrl = prepared.webhookUrl;
    webhookState = prepared.webhookState;
    creatorStatements = buildPreparedCreatorStatements(env, prepared, mutationGuard);
    clearPendingThrone = true;
  } else

  if (input.existingCreatorId !== null) {
    const owned = await env.DB.prepare("SELECT id FROM throne_creators WHERE id = ? AND owner_discord_user_id = ?")
      .bind(input.existingCreatorId, draft.owner_user_id)
      .first();
    if (!owned) badRequest("throne_creator_not_owned", "that Throne creator is not owned by this user");
    creatorId = input.existingCreatorId;
    if (input.rotateWebhook) {
      const rotated = await prepareWebhookSecret(env, creatorId);
      webhookUrl = rotated.webhookUrl;
      webhookState = "rotated";
      creatorStatements = [
        env.DB.prepare(
          `UPDATE throne_creators
               SET route_secret_hash = ?, webhook_verified_at = NULL, updated_at = ?
            WHERE id = ? AND owner_discord_user_id = ? AND ${mutationGuard.sql}`,
        ).bind(
          rotated.routeSecretHash,
          nowIso(),
          creatorId,
          draft.owner_user_id,
          ...mutationGuard.params,
        ),
      ];
    }
  } else {
    let prepared;
    try {
      prepared = await prepareThroneCreatorAttachment(
        env,
        draft.owner_user_id,
        input.throneInput as string,
        { rotateWebhook: input.rotateWebhook },
      );
    } catch (error) {
      if (error instanceof ThroneResolutionError) badRequest(error.code, error.message);
      throw error;
    }
    creatorId = prepared.creatorId;
    webhookUrl = prepared.webhookUrl;
    webhookState = prepared.webhookState;
    creatorStatements = buildPreparedCreatorStatements(env, prepared, mutationGuard);
  }

  const newSnapshot: DocumentSnapshot = { ...current, throneCreatorId: creatorId };
  const contract = await bumpRevisionAndReturn(env, draft, newSnapshot, creatorStatements, { clearPendingThrone });
  return { draft: contract, webhookUrl, webhookState };
}

export interface RotateThroneInput {
  readonly draftId: string;
  readonly ownerUserId: string;
  readonly expectedRevision: number;
}

/** Explicitly rotates the webhook secret for whichever Throne creator is currently connected to
 * this draft's document. Requires a creator to already be attached (via `attachThroneToDraft`). */
export async function rotateDraftThroneWebhook(env: Env, input: RotateThroneInput): Promise<ThroneDraftResult> {
  const { draft, current, governingOrientation } = await loadMutableDraft(
    env,
    input.draftId,
    input.ownerUserId,
    input.expectedRevision,
  );
  requireThroneCapableDraft(draft, governingOrientation);
  if (current.throneCreatorId === null) {
    badRequest("throne_not_connected", "no Throne creator is connected to this draft yet");
  }
  const owned = await env.DB.prepare("SELECT id FROM throne_creators WHERE id = ? AND owner_discord_user_id = ?")
    .bind(current.throneCreatorId, draft.owner_user_id)
    .first();
  if (!owned) badRequest("throne_creator_not_owned", "that Throne creator is not owned by this user");

  const rotated = await prepareWebhookSecret(env, current.throneCreatorId);
  const mutationGuard = draftMutationGuard(draft);
  const rotateStatement = env.DB.prepare(
    `UPDATE throne_creators
        SET route_secret_hash = ?, webhook_verified_at = NULL, updated_at = ?
      WHERE id = ? AND owner_discord_user_id = ? AND ${mutationGuard.sql}`,
  ).bind(
    rotated.routeSecretHash,
    nowIso(),
    current.throneCreatorId,
    draft.owner_user_id,
    ...mutationGuard.params,
  );
  // Secret rotation and the draft CAS share one D1 batch. A stale caller's
  // guarded creator update is a no-op, so it can never invalidate the URL
  // belonging to the winning request.
  const contract = await bumpRevisionAndReturn(env, draft, null, [rotateStatement]);
  return { draft: contract, webhookUrl: rotated.webhookUrl, webhookState: "rotated" };
}

export interface ThroneStatusInput {
  readonly draftId: string;
  readonly ownerUserId: string;
  readonly expectedRevision: number;
}

/**
 * The deliberately tiny, secret-free view of a draft's Throne connection.
 *
 * `verified` reflects `throne_creators.webhook_verified_at`, which the public
 * webhook route stamps the first time Throne delivers a correctly signed
 * payload (including its "test webhook" button) -- so this is a genuine
 * proof-of-delivery check, re-read live on every call rather than cached on
 * the draft. Nothing identifying or sensitive is included: no creator id, no
 * route secret or its hash, no webhook URL, no public Throne creator id.
 */
export interface ThroneDraftStatus {
  readonly handle: string | null;
  readonly verified: boolean;
  readonly verifiedAt: string | null;
}

/**
 * `GET`-side counterpart to `attachThroneToDraft`: reports whether Throne has
 * actually delivered a signed webhook for the creator connected to this
 * draft. It is not a mutation, but it still requires the caller's
 * `expected_revision` to match exactly, so a stale wizard message can never
 * flip its own UI to "verified" using a status that belongs to a newer state
 * of the draft (for instance one whose secret was rotated since).
 */
export async function getDraftThroneStatus(env: Env, input: ThroneStatusInput): Promise<ThroneDraftStatus> {
  const draft = await loadOwnedDraft(env, input.draftId, input.ownerUserId);
  if (draft.status !== "active") conflict("draft_not_active", "this draft has already been published or restarted");
  if (draft.revision !== input.expectedRevision) {
    conflict("stale_revision", "expected_revision does not match the draft's current revision");
  }

  const current = (await readDocumentSnapshot(env, draft.document_id)) ?? EMPTY_SNAPSHOT;
  const governingOrientation = await resolveGoverningOrientation(env, draft, current);
  requireThroneCapableDraft(draft, governingOrientation);

  if (current.throneCreatorId === null) {
    return { handle: null, verified: false, verifiedAt: null };
  }
  const creator = await env.DB.prepare(
    "SELECT handle, webhook_verified_at FROM throne_creators WHERE id = ? AND owner_discord_user_id = ?",
  )
    .bind(current.throneCreatorId, draft.owner_user_id)
    .first<{ handle: string; webhook_verified_at: string | null }>();
  if (creator === null) {
    // The document points at a creator this user does not own (or that no longer
    // exists). Report "not connected" rather than leaking that it exists at all.
    return { handle: null, verified: false, verifiedAt: null };
  }
  return {
    handle: creator.handle,
    verified: creator.webhook_verified_at !== null,
    verifiedAt: creator.webhook_verified_at,
  };
}
