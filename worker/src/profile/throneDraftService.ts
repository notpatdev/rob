/**
 * Throne connection for a profile draft: resolving/attaching a Throne
 * creator (new or already-owned) to the draft's document, and explicit
 * webhook secret rotation. This is the draft-scoped counterpart to
 * `throne/creatorService.ts`'s guild-scoped `/register` flow -- both share
 * the same resolution/secret-issuance code, but only this module writes
 * the connection into a profile document rather than a guild registration.
 */
import type { Env } from "../env.js";
import { nowIso } from "../util/id.js";
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
  prepareThroneCreatorAttachment,
  prepareWebhookSecret,
  ThroneResolutionError,
  type SqlMutationGuard,
  type WebhookState,
} from "../throne/creatorService.js";

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
          SET revision = ?, updated_at = ?
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

export interface AttachThroneInput {
  readonly draftId: string;
  readonly ownerUserId: string;
  readonly expectedRevision: number;
  /** A Throne username/profile URL to resolve, mutually exclusive with `existingCreatorId`. */
  readonly throneInput: string | null;
  /** An id from this draft's `thronePrefill.ownedCreators`, to reattach a creator already owned
   * by this user without re-resolving it against Throne. */
  readonly existingCreatorId: string | null;
  readonly rotateWebhook: boolean;
}

export interface ThroneDraftResult {
  readonly draft: DraftContract;
  readonly webhookUrl: string | null;
  readonly webhookState: WebhookState | "unchanged";
}

/** Resolves/attaches a Throne creator to this draft's document. Exactly one of `throneInput`/
 * `existingCreatorId` must be supplied; a webhook secret is only ever returned in plaintext here,
 * on the one request that issues or rotates it. */
export async function attachThroneToDraft(env: Env, input: AttachThroneInput): Promise<ThroneDraftResult> {
  const { draft, current, governingOrientation } = await loadMutableDraft(
    env,
    input.draftId,
    input.ownerUserId,
    input.expectedRevision,
  );
  requireThroneCapableDraft(draft, governingOrientation);

  if ((input.throneInput === null) === (input.existingCreatorId === null)) {
    badRequest("throne_input_required", "exactly one of throne_input or existing_creator_id is required");
  }

  let creatorId: string;
  let webhookUrl: string | null = null;
  let webhookState: WebhookState | "unchanged" = "unchanged";
  let creatorStatements: D1PreparedStatement[] = [];
  const mutationGuard = draftMutationGuard(draft);

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
              SET route_secret_hash = ?, updated_at = ?
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
  const contract = await bumpRevisionAndReturn(env, draft, newSnapshot, creatorStatements);
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
        SET route_secret_hash = ?, updated_at = ?
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
