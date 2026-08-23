/**
 * Atomic profile publication.
 *
 * D1 does not give a Worker a way to hold open a multi-round-trip
 * transaction, so "publish only if the root is still at the version this
 * draft was based on" is implemented as a single compare-and-swap: the
 * root row's `INSERT ... ON CONFLICT DO UPDATE ... WHERE version = ?old`
 * either lands (a fresh row, or an existing one still at `old`) or is
 * silently skipped by SQLite's upsert semantics (a conflict whose WHERE
 * is false makes the whole ON CONFLICT clause a no-op, not an error).
 * Every other statement in the same batch is guarded by an `EXISTS`
 * subquery checking the root *already reflects the new version+document*,
 * so if the CAS above didn't land, nothing else in the batch takes effect
 * either -- one D1 batch, one atomic outcome, no partial publish.
 */
import type { Env } from "../env.js";
import { newId, nowIso } from "../util/id.js";
import { LIMITS, ORIENTATION_CAPABILITIES, stepsForDraft, type StepKey } from "./contracts.js";
import { readDocumentSnapshot } from "./documentStore.js";
import { resolveProfile, type ResolvedProfile } from "./resolver.js";
import { DM_STATUS_SELECTION_STEP_KEY, DraftError } from "./draftService.js";
import {
  buildRegistrationProjectionStatements,
  collectPublishRegistrationProjections,
  hasLegacyRegistrationConflict,
  legacyRegistrationConflictGuard,
} from "./registrationSync.js";

interface DraftRow {
  id: string;
  owner_user_id: string;
  origin_guild_id: string | null;
  target_scope: "global" | "server";
  guild_id: string | null;
  server_mode: "linked" | "independent" | null;
  document_id: string;
  base_version: number;
  status: "active" | "published";
  revision: number;
}

function notFound(): never {
  throw new DraftError(404, "draft_not_found", "Draft not found");
}
function conflict(code: string, message: string): never {
  throw new DraftError(409, code, message);
}
function badRequest(code: string, message: string): never {
  throw new DraftError(400, code, message);
}

function isConstraintError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  return /constraint failed/i.test(message);
}

async function loadOwnedActiveDraft(env: Env, draftId: string, ownerUserId: string): Promise<DraftRow> {
  const row = await env.DB.prepare("SELECT * FROM profile_drafts WHERE id = ?").bind(draftId).first<DraftRow>();
  if (row === null || row.owner_user_id !== ownerUserId) notFound();
  return row;
}

export interface PublishDraftInput {
  readonly draftId: string;
  readonly ownerUserId: string;
  readonly expectedRevision: number;
}

export async function publishDraft(env: Env, input: PublishDraftInput): Promise<ResolvedProfile> {
  const draft = await loadOwnedActiveDraft(env, input.draftId, input.ownerUserId);
  if (draft.status !== "active") conflict("draft_not_active", "this draft has already been published");
  if (draft.revision !== input.expectedRevision) {
    conflict("stale_revision", "expected_revision does not match the draft's current revision");
  }

  const snapshot = await readDocumentSnapshot(env, draft.document_id);
  if (snapshot === null) notFound();

  const linked = draft.target_scope === "server" && draft.server_mode === "linked";
  let governingOrientation = snapshot.orientation;
  let governingCreatorId = snapshot.throneCreatorId;
  if (linked) {
    const globalRoot = await env.DB.prepare("SELECT current_document_id FROM global_profiles WHERE owner_user_id = ?")
      .bind(draft.owner_user_id)
      .first<{ current_document_id: string }>();
    if (globalRoot === null) {
      badRequest("global_profile_required", "a linked server profile requires an existing published global profile");
    }
    const globalSnapshot = await readDocumentSnapshot(env, globalRoot.current_document_id);
    governingOrientation = globalSnapshot?.orientation ?? null;
    governingCreatorId = globalSnapshot?.throneCreatorId ?? null;
  }
  if (governingOrientation === null) {
    badRequest("orientation_required", "orientation must be chosen before publishing");
  }
  if (governingCreatorId !== null) {
    const verifiedCreator = await env.DB.prepare(
      `SELECT id
         FROM throne_creators
        WHERE id = ? AND owner_discord_user_id = ? AND webhook_verified_at IS NOT NULL`,
    )
      .bind(governingCreatorId, draft.owner_user_id)
      .first();
    if (verifiedCreator === null) {
      badRequest(
        "throne_webhook_unverified",
        "the connected Throne creator must pass Test Webhook before this profile can be published",
      );
    }
  }

  const requiredSteps = stepsForDraft(draft.target_scope, draft.server_mode, governingOrientation).filter(
    (step): step is Exclude<StepKey, "review"> => step !== "review",
  );
  const { results: stepRows } = await env.DB.prepare(
    "SELECT step_key, status FROM profile_draft_steps WHERE draft_id = ?",
  )
    .bind(draft.id)
    .all<{ step_key: string; status: "pending" | "completed" }>();
  const completed = new Set(stepRows.filter((row) => row.status === "completed").map((row) => row.step_key));
  const missing = requiredSteps.filter((step) => !completed.has(step));
  if (missing.length > 0) {
    badRequest("steps_incomplete", `the following steps must be completed before publishing: ${missing.join(", ")}`);
  }
  if (!completed.has(DM_STATUS_SELECTION_STEP_KEY)) {
    badRequest(
      "dm_status_selection_required",
      "a DM status or linked inheritance must be explicitly selected before publishing",
    );
  }
  if (!linked && snapshot.dmStatus === null) {
    badRequest("dm_status_required", "dm_status must be chosen before publishing");
  }

  const caps = ORIENTATION_CAPABILITIES[governingOrientation];
  if (!linked) {
    const paymentLinks = snapshot.links.filter((link) => link.linkType === "payment");
    if (paymentLinks.length > 0 && !caps.payment) {
      badRequest("payment_links_unavailable", "this orientation does not support payment links");
    }

    if (snapshot.links.length > LIMITS.linkMaxCount) {
      badRequest("too_many_links", `at most ${LIMITS.linkMaxCount} links are allowed`);
    }
  }

  const registrationProjections = await collectPublishRegistrationProjections(env, {
    targetScope: draft.target_scope,
    guildId: draft.guild_id,
    serverMode: draft.server_mode,
    ownerUserId: draft.owner_user_id,
    creatorId: governingCreatorId,
  });
  if (await hasLegacyRegistrationConflict(env, registrationProjections)) {
    conflict(
      "legacy_registration_conflict",
      "an explicit v1 Throne registration conflicts with this profile; resolve it before publishing",
    );
  }
  const legacyGuard = legacyRegistrationConflictGuard(registrationProjections);
  const throneVerificationGuardSql =
    governingCreatorId === null
      ? "1 = 1"
      : `EXISTS (
           SELECT 1 FROM throne_creators
            WHERE id = ? AND owner_discord_user_id = ? AND webhook_verified_at IS NOT NULL
         )`;
  const throneVerificationGuardParams =
    governingCreatorId === null ? [] : [governingCreatorId, draft.owner_user_id];

  const now = nowIso();
  const isGlobal = draft.target_scope === "global";
  const rootTable = isGlobal ? "global_profiles" : "server_profiles";
  const rootKeyWhereSql = isGlobal ? "owner_user_id = ?" : "guild_id = ? AND owner_user_id = ?";
  const rootKeyParams: unknown[] = isGlobal ? [draft.owner_user_id] : [draft.guild_id, draft.owner_user_id];

  const rootRow = isGlobal
    ? await env.DB.prepare("SELECT current_document_id, version FROM global_profiles WHERE owner_user_id = ?")
        .bind(draft.owner_user_id)
        .first<{ current_document_id: string; version: number }>()
    : await env.DB.prepare(
        "SELECT current_document_id, version FROM server_profiles WHERE guild_id = ? AND owner_user_id = ?",
      )
        .bind(draft.guild_id, draft.owner_user_id)
        .first<{ current_document_id: string; version: number }>();

  // The draft's captured base version is the publication CAS. Reading the
  // root's current version here and then using that as the expected value
  // would let an old draft overwrite a publication that landed after the
  // draft started.
  const oldVersion = draft.base_version;
  const newVersion = draft.base_version + 1;
  const oldDocumentId = rootRow?.current_document_id ?? null;
  const newDocumentId = draft.document_id;

  const newDraftRevision = draft.revision + 1;
  const publishedDraftGuardSql =
    "EXISTS (SELECT 1 FROM profile_drafts WHERE id = ? AND revision = ? AND status = 'published')";
  const publishedDraftGuardParams = [draft.id, newDraftRevision];
  const publishedRootGuardSql = `EXISTS (SELECT 1 FROM ${rootTable} WHERE ${rootKeyWhereSql} AND version = ? AND current_document_id = ?)`;
  const publishedRootGuardParams = [...rootKeyParams, newVersion, newDocumentId];
  const publishGuardSql = `${publishedDraftGuardSql} AND ${publishedRootGuardSql}`;
  const publishGuardParams = [...publishedDraftGuardParams, ...publishedRootGuardParams];

  const baseRootGuardSql =
    oldVersion === 0
      ? `NOT EXISTS (SELECT 1 FROM ${rootTable} WHERE ${rootKeyWhereSql})`
      : `EXISTS (SELECT 1 FROM ${rootTable} WHERE ${rootKeyWhereSql} AND version = ?)`;
  const baseRootGuardParams = oldVersion === 0 ? rootKeyParams : [...rootKeyParams, oldVersion];

  const rootUpsertStatement = isGlobal
    ? env.DB.prepare(
        `INSERT INTO global_profiles (owner_user_id, current_document_id, version, published_at, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?)
         ON CONFLICT (owner_user_id) DO UPDATE SET
           current_document_id = excluded.current_document_id,
           version = excluded.version,
           published_at = excluded.published_at,
           updated_at = excluded.updated_at
         WHERE global_profiles.version = ?`,
      ).bind(draft.owner_user_id, newDocumentId, newVersion, now, now, now, oldVersion)
    : env.DB.prepare(
        `INSERT INTO server_profiles (id, guild_id, owner_user_id, mode, current_document_id, version, published_at, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT (guild_id, owner_user_id) DO UPDATE SET
           mode = excluded.mode,
           current_document_id = excluded.current_document_id,
           version = excluded.version,
           published_at = excluded.published_at,
           updated_at = excluded.updated_at
         WHERE server_profiles.version = ?`,
      ).bind(
        newId(),
        draft.guild_id,
        draft.owner_user_id,
        draft.server_mode,
        newDocumentId,
        newVersion,
        now,
        now,
        now,
        oldVersion,
      );

  const statements: D1PreparedStatement[] = [
    env.DB.prepare(
      `UPDATE profile_drafts
          SET status = 'published', revision = ?, published_at = ?, updated_at = ?
      WHERE id = ? AND revision = ? AND status = 'active'
        AND EXISTS (SELECT 1 FROM profile_documents WHERE id = ? AND state = 'draft')`,
    ).bind(newDraftRevision, now, now, draft.id, draft.revision, draft.document_id),
    // A zero-row CAS is not an error in SQLite. Feeding its guarded scalar
    // result into the NOT NULL document_id column converts a stale draft/root
    // into a real constraint failure, which makes D1 roll back this whole
    // batch instead of leaving either side half-published.
    env.DB.prepare(
      `INSERT INTO profile_publications
         (id, profile_kind, owner_user_id, guild_id, version, document_id, published_at)
       VALUES (
         ?, ?, ?, ?, ?,
         (SELECT document_id
            FROM profile_drafts
           WHERE id = ? AND revision = ? AND status = 'published'
             AND document_id = ? AND ${baseRootGuardSql} AND ${legacyGuard.sql}
             AND ${throneVerificationGuardSql}),
         ?
       )`,
    ).bind(
      newId(),
      isGlobal ? "global" : "server",
      draft.owner_user_id,
      draft.guild_id,
      newVersion,
      draft.id,
      newDraftRevision,
      newDocumentId,
      ...baseRootGuardParams,
      ...legacyGuard.params,
      ...throneVerificationGuardParams,
      now,
    ),
    rootUpsertStatement,
    env.DB.prepare(
      `UPDATE profile_documents
          SET state = 'published', updated_at = ?
        WHERE id = ? AND state = 'draft' AND ${publishGuardSql}`,
    ).bind(now, newDocumentId, ...publishGuardParams),
  ];

  if (oldDocumentId !== null) {
    statements.push(
      env.DB.prepare(
        `UPDATE profile_documents
            SET state = 'superseded', updated_at = ?
          WHERE id = ? AND state = 'published' AND ${publishGuardSql}`,
      ).bind(now, oldDocumentId, ...publishGuardParams),
    );
  }

  statements.push(
    ...buildRegistrationProjectionStatements(env, registrationProjections, {
      sql: publishGuardSql,
      params: publishGuardParams,
    }),
  );

  let results: D1Result[];
  try {
    results = await env.DB.batch(statements);
  } catch (error) {
    if (!isConstraintError(error)) throw error;
    if (await hasLegacyRegistrationConflict(env, registrationProjections)) {
      conflict(
        "legacy_registration_conflict",
        "an explicit v1 Throne registration conflicts with this profile; resolve it before publishing",
      );
    }
    // A genuinely concurrent publish of this same draft can race past the
    // initial read. The guarded NOT NULL publication insert and the unique
    // document history constraint turn every losing path into a real,
    // batch-aborting error instead of a partial state change.
    conflict("publish_conflict", "the profile changed underneath this draft; reload and try again");
  }
  const draftResult = results[0];
  const publicationResult = results[1];
  const rootResult = results[2];
  if (
    draftResult === undefined ||
    draftResult.meta.changes === 0 ||
    publicationResult === undefined ||
    publicationResult.meta.changes === 0 ||
    rootResult === undefined ||
    rootResult.meta.changes === 0
  ) {
    conflict("publish_conflict", "the profile changed underneath this draft; reload and try again");
  }

  const guildIdForResolution = isGlobal ? env.BILL_HOME_GUILD_ID : (draft.guild_id as string);
  const { profile } = await resolveProfile(env, guildIdForResolution, draft.owner_user_id);
  if (profile === null) {
    // Should be unreachable given the CAS above succeeded, but fail loudly rather than lie.
    throw new DraftError(500, "publish_resolution_failed", "Published successfully but could not resolve the new profile");
  }

  return profile;
}
