/**
 * Draft lifecycle: start (create/resume), read, step mutation, and
 * restart. Every mutating operation is optimistic-concurrency-checked
 * against the draft's `revision`: the caller must echo back the revision
 * it last saw as `expectedRevision`, and a mismatch means somebody else
 * (a concurrent request, a second device, a race with restart) already
 * changed the draft first.
 *
 * D1 batches commit all-or-nothing, but a statement whose WHERE clause
 * simply matches zero rows is *not* an error -- so "revision doesn't
 * match" cannot by itself stop the rest of a batch from applying. Every
 * mutation here is instead structured as: the very first statement in the
 * batch is the compare-and-swap (`UPDATE ... WHERE revision = ?old`), and
 * every earlier write is guarded by that same old revision. D1 serializes
 * each atomic batch, so a losing batch starts after the winner committed
 * the next revision: all of its guarded writes and final CAS become no-ops.
 */
import type { Env } from "../env.js";
import { requireHomeGuildId } from "../env.js";
import { newId, nowIso } from "../util/id.js";
import { isSnowflake } from "../util/snowflake.js";
import {
  ValidationError,
  WIZARD_STAGES,
  isWizardStage,
  parseIdentityStep,
  parseLinkStep,
  parseLinkedIdentityStep,
  parseLinkedLinksStep,
  parseOrientationStep,
  parseThroneStep,
  parseWizardStageUpdate,
  stepsForDraft,
  wizardStagesForDraft,
  ORIENTATION_CAPABILITIES,
  LIMITS,
  type Orientation,
  type ServerMode,
  type StepKey,
  type TargetScope,
  type WizardStage,
} from "./contracts.js";
import {
  EMPTY_SNAPSHOT,
  buildDocumentWriteStatements,
  readDocumentSnapshot,
  type DocumentSnapshot,
} from "./documentStore.js";

export const DM_STATUS_SELECTION_STEP_KEY = "identity_dm_status_selected";

export class DraftError extends Error {
  readonly status: number;
  readonly code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export function notFound(message = "Draft not found"): never {
  throw new DraftError(404, "draft_not_found", message);
}
export function conflict(code: string, message: string): never {
  throw new DraftError(409, code, message);
}
export function badRequest(code: string, message: string): never {
  throw new DraftError(400, code, message);
}

export interface DraftRow {
  id: string;
  owner_user_id: string;
  origin_guild_id: string | null;
  target_scope: TargetScope;
  guild_id: string | null;
  server_mode: ServerMode | null;
  document_id: string;
  base_version: number;
  status: "active" | "published";
  current_step: StepKey;
  revision: number;
  /** NULL on every draft that predates migration 0004, and on any draft whose owner has not
   * navigated since; `buildContract` derives a resume position rather than exposing the NULL. */
  wizard_stage: string | null;
  wizard_substep: string | null;
  /** Staged, not-yet-confirmed Throne identity (see migration 0004 and `throneDraftService`).
   * Only the confirmation capability's hash is stored; nothing exists in `throne_creators`
   * and no webhook secret has been minted while these are set. */
  pending_throne_token_hash: string | null;
  pending_throne_public_creator_id: string | null;
  pending_throne_handle: string | null;
  pending_throne_profile_url: string | null;
  pending_throne_expires_at: string | null;
  created_at: string;
  updated_at: string;
  published_at: string | null;
}

/** Exported so link/Throne draft services (which mutate the same draft/document rows via their
 * own dedicated endpoints) share exactly one "load and ownership-check a draft" implementation. */
export async function loadOwnedDraft(env: Env, draftId: string, ownerUserId: string): Promise<DraftRow> {
  const row = await env.DB.prepare("SELECT * FROM profile_drafts WHERE id = ?").bind(draftId).first<DraftRow>();
  if (row === null || row.owner_user_id !== ownerUserId) notFound();
  return row;
}

interface StepStatusRow {
  step_key: string;
  status: "pending" | "completed";
  completed_at: string | null;
}

/** A `linked` server overlay inherits orientation/Throne from the owner's global document, which
 * changes both its step sequence and its wizard stage sequence. */
function linkedDraft(draft: DraftRow): boolean {
  return draft.target_scope === "server" && draft.server_mode === "linked";
}

async function loadStepStatuses(env: Env, draftId: string): Promise<Map<string, StepStatusRow>> {
  const { results } = await env.DB.prepare(
    "SELECT step_key, status, completed_at FROM profile_draft_steps WHERE draft_id = ?",
  )
    .bind(draftId)
    .all<StepStatusRow>();
  return new Map(results.map((row) => [row.step_key, row]));
}

/** The orientation that governs a draft's capabilities: its own for global/independent, or the live global owner's for a linked overlay. */
export async function resolveGoverningOrientation(env: Env, draft: DraftRow, ownDocument: DocumentSnapshot): Promise<Orientation | null> {
  if (!linkedDraft(draft)) {
    return ownDocument.orientation;
  }
  const globalRoot = await env.DB.prepare("SELECT current_document_id FROM global_profiles WHERE owner_user_id = ?")
    .bind(draft.owner_user_id)
    .first<{ current_document_id: string }>();
  if (globalRoot === null) return null;
  const globalDoc = await readDocumentSnapshot(env, globalRoot.current_document_id);
  return globalDoc?.orientation ?? null;
}

export interface DraftContract {
  readonly id: string;
  readonly ownerUserId: string;
  readonly originGuildId: string | null;
  readonly targetScope: TargetScope;
  readonly guildId: string | null;
  readonly serverMode: ServerMode | null;
  readonly status: "active" | "published";
  readonly revision: number;
  readonly baseVersion: number;
  readonly currentStep: StepKey;
  readonly nextStep: StepKey | null;
  readonly steps: { key: StepKey; status: "pending" | "completed"; completedAt: string | null }[];
  readonly dmStatusSelected: boolean;
  readonly governingOrientation: Orientation | null;
  /** The wizard screen this draft should resume on. Never null, even for a draft whose stored
   * bookmark is still NULL (pre-0004 rows, or a draft nobody has navigated yet): see
   * `deriveWizardStage`. Always one of `wizardStagesForDraft` for this draft. */
  readonly wizardStage: WizardStage;
  /** A free-form marker scoped to `wizardStage` only (e.g. Throne verification state, or the
   * "came here from review" return marker); null unless the last navigation named one. */
  readonly wizardSubstep: string | null;
  /** A Throne handle this draft has resolved but whose owner has not confirmed yet, so the
   * confirmation screen survives a bot restart. Deliberately carries no creator id, no public
   * Throne id, and never the confirmation token itself -- only what the owner is being asked
   * to say yes to. Null once confirmed, expired-and-replaced, or never resolved. */
  readonly thronePending: { handle: string; expiresAt: string | null } | null;
  readonly document: {
    dmStatus: DocumentSnapshot["dmStatus"];
    bio: string | null;
    publicSendStats: boolean;
    selections: DocumentSnapshot["selections"];
    aliases: string[];
    links: DocumentSnapshot["links"];
    overriddenFields: readonly string[];
    hiddenInheritedLinkIds: readonly string[];
    throneCreatorId: string | null;
    preferredPaymentLinkId: string | null;
    profileColor: number | null;
  };
  /** Only present when the governing orientation has the Throne capability; lets the wizard
   * offer "reuse your existing Throne creator" / "this guild already has a registration"
   * instead of always starting Throne resolution from scratch. */
  readonly thronePrefill: {
    ownedCreators: { id: string; handle: string }[];
    existingRegistrationCreatorId: string | null;
  } | null;
  readonly createdAt: string;
  readonly updatedAt: string;
  readonly publishedAt: string | null;
}

interface OwnedCreatorRow {
  id: string;
  handle: string;
}

/** The guild a Throne registration would apply to for this draft: the home guild for a global
 * draft (registrations there back the home-guild fan-out), or the draft's own guild otherwise. */
function registrationGuildIdForDraft(env: Env, draft: DraftRow): string | null {
  if (draft.target_scope === "global") {
    try {
      return requireHomeGuildId(env);
    } catch {
      return null;
    }
  }
  return draft.guild_id;
}

async function loadThronePrefill(
  env: Env,
  draft: DraftRow,
  governingOrientation: Orientation | null,
): Promise<DraftContract["thronePrefill"]> {
  if (governingOrientation === null || !ORIENTATION_CAPABILITIES[governingOrientation].throne) return null;

  const { results: ownedCreators } = await env.DB.prepare(
    "SELECT id, handle FROM throne_creators WHERE owner_discord_user_id = ? ORDER BY updated_at DESC",
  )
    .bind(draft.owner_user_id)
    .all<OwnedCreatorRow>();

  const registrationGuildId = registrationGuildIdForDraft(env, draft);
  let existingRegistrationCreatorId: string | null = null;
  if (registrationGuildId !== null) {
    const registration = await env.DB.prepare(
      "SELECT creator_id FROM domme_registrations WHERE guild_id = ? AND discord_user_id = ? AND active = 1",
    )
      .bind(registrationGuildId, draft.owner_user_id)
      .first<{ creator_id: string }>();
    existingRegistrationCreatorId = registration?.creator_id ?? null;
  }

  return { ownedCreators, existingRegistrationCreatorId };
}

/**
 * The coarse step a stage belongs to, used only to translate a step-based
 * resume position into a stage-based one.
 */
const STAGE_FOR_STEP: Readonly<Record<StepKey, WizardStage>> = {
  orientation: "orientation",
  identity: "pronouns",
  links: "links",
  throne: "throne",
  review: "review",
};

/**
 * The screen a draft resumes on.
 *
 * A stored bookmark wins whenever it is still part of this draft's stage
 * sequence. Otherwise -- a draft created before migration 0004 added the
 * columns, a draft nobody has navigated since, or a bookmark the owner
 * invalidated by changing orientation (say, `throne` after switching to
 * `submissive`) -- the position is *derived*, deterministically, from the
 * draft's own progress: the first still-pending step, else the last step it
 * touched, mapped to that step's first stage and then clamped backwards to
 * the nearest applicable stage. Deriving rather than persisting a guess is
 * what lets 0004 stay purely additive while old active drafts still resume
 * exactly where their coarse `current_step` left them.
 */
export function deriveWizardStage(
  stages: readonly WizardStage[],
  nextStep: StepKey | null,
  currentStep: StepKey,
): WizardStage {
  const target = STAGE_FOR_STEP[nextStep ?? currentStep] ?? "review";
  if (stages.includes(target)) return target;
  const targetIndex = WIZARD_STAGES.indexOf(target);
  const applicable = stages.filter((stage) => WIZARD_STAGES.indexOf(stage) <= targetIndex);
  return applicable.at(-1) ?? (stages[0] as WizardStage);
}

function resolveWizardStage(
  draft: DraftRow,
  stages: readonly WizardStage[],
  nextStep: StepKey | null,
): WizardStage {
  const stored = draft.wizard_stage;
  if (isWizardStage(stored) && stages.includes(stored)) return stored;
  return deriveWizardStage(stages, nextStep, draft.current_step);
}

export async function buildContract(env: Env, draft: DraftRow): Promise<DraftContract> {
  const snapshot = (await readDocumentSnapshot(env, draft.document_id)) ?? EMPTY_SNAPSHOT;
  const governingOrientation = await resolveGoverningOrientation(env, draft, snapshot);
  const steps = stepsForDraft(draft.target_scope, draft.server_mode, governingOrientation);
  const statuses = await loadStepStatuses(env, draft.id);
  const dmStatusSelected =
    statuses.get(DM_STATUS_SELECTION_STEP_KEY)?.status === "completed";
  const stepList = steps.map((key) => {
    const found = statuses.get(key);
    const completed =
      found?.status === "completed" && (key !== "identity" || dmStatusSelected);
    return {
      key,
      status: completed ? ("completed" as const) : ("pending" as const),
      completedAt: completed ? (found?.completed_at ?? null) : null,
    };
  });
  const nextStep = stepList.find((step) => step.status === "pending")?.key ?? null;
  const thronePrefill = await loadThronePrefill(env, draft, governingOrientation);
  const stages = wizardStagesForDraft(draft.target_scope, draft.server_mode, governingOrientation);

  return {
    id: draft.id,
    ownerUserId: draft.owner_user_id,
    originGuildId: draft.origin_guild_id,
    targetScope: draft.target_scope,
    guildId: draft.guild_id,
    serverMode: draft.server_mode,
    status: draft.status,
    revision: draft.revision,
    baseVersion: draft.base_version,
    currentStep: draft.current_step,
    nextStep,
    steps: stepList,
    dmStatusSelected,
    governingOrientation,
    wizardStage: resolveWizardStage(draft, stages, nextStep),
    wizardSubstep: draft.wizard_substep,
    thronePending:
      draft.pending_throne_token_hash === null || draft.pending_throne_handle === null
        ? null
        : { handle: draft.pending_throne_handle, expiresAt: draft.pending_throne_expires_at },
    document: {
      dmStatus: snapshot.dmStatus,
      bio: snapshot.bio,
      publicSendStats: snapshot.publicSendStats,
      selections: snapshot.selections,
      aliases: snapshot.aliases,
      links: snapshot.links,
      overriddenFields: snapshot.overriddenFields,
      hiddenInheritedLinkIds: snapshot.hiddenInheritedLinkIds,
      throneCreatorId: snapshot.throneCreatorId,
      preferredPaymentLinkId: snapshot.preferredPaymentLinkId,
      profileColor: snapshot.profileColor,
    },
    thronePrefill,
    createdAt: draft.created_at,
    updatedAt: draft.updated_at,
    publishedAt: draft.published_at,
  };
}

export async function getDraftContract(env: Env, draftId: string, ownerUserId: string): Promise<DraftContract> {
  const draft = await loadOwnedDraft(env, draftId, ownerUserId);
  return buildContract(env, draft);
}

// --- start -----------------------------------------------------------------------------------

export interface StartDraftInput {
  readonly ownerUserId: string;
  readonly originGuildId: string;
  readonly targetScope: TargetScope;
  readonly guildId: string | null;
  readonly serverMode: ServerMode | null;
}

export interface StartDraftResult {
  readonly resumeRequired: boolean;
  readonly draft: DraftContract;
}

async function findActiveDraftRow(env: Env, input: StartDraftInput): Promise<DraftRow | null> {
  if (input.targetScope === "global") {
    return env.DB.prepare(
      "SELECT * FROM profile_drafts WHERE owner_user_id = ? AND target_scope = 'global' AND status = 'active'",
    )
      .bind(input.ownerUserId)
      .first<DraftRow>();
  }
  return env.DB.prepare(
    "SELECT * FROM profile_drafts WHERE owner_user_id = ? AND guild_id = ? AND target_scope = 'server' AND status = 'active'",
  )
    .bind(input.ownerUserId, input.guildId)
    .first<DraftRow>();
}

/** Loads the current published document (if any) as a starting snapshot, or an empty one. */
function cloneSnapshotForDraft(snapshot: DocumentSnapshot): DocumentSnapshot {
  const remappedLinkIds = new Map<string, string>();
  const links = snapshot.links.map((link) => {
    const clonedId = newId();
    if (link.id !== null) remappedLinkIds.set(link.id, clonedId);
    return { ...link, id: clonedId };
  });
  const preferredPaymentLinkId =
    snapshot.preferredPaymentLinkId === null
      ? null
      : (remappedLinkIds.get(snapshot.preferredPaymentLinkId) ?? snapshot.preferredPaymentLinkId);
  return { ...snapshot, links, preferredPaymentLinkId };
}

async function loadStartingSnapshot(env: Env, input: StartDraftInput): Promise<{ snapshot: DocumentSnapshot; baseVersion: number }> {
  if (input.targetScope === "global") {
    const root = await env.DB.prepare("SELECT current_document_id, version FROM global_profiles WHERE owner_user_id = ?")
      .bind(input.ownerUserId)
      .first<{ current_document_id: string; version: number }>();
    if (root === null) return { snapshot: EMPTY_SNAPSHOT, baseVersion: 0 };
    const snapshot = await readDocumentSnapshot(env, root.current_document_id);
    return {
      snapshot: snapshot === null ? EMPTY_SNAPSHOT : cloneSnapshotForDraft(snapshot),
      baseVersion: root.version,
    };
  }

  const root = await env.DB.prepare(
    "SELECT current_document_id, version, mode FROM server_profiles WHERE guild_id = ? AND owner_user_id = ?",
  )
    .bind(input.guildId, input.ownerUserId)
    .first<{ current_document_id: string; version: number; mode: ServerMode }>();
  // A prior root in a *different* mode than requested isn't cloned: converting
  // linked<->independent starts fresh rather than attempting to materialize
  // one shape into the other. This is a known, documented limitation.
  if (root === null || root.mode !== input.serverMode) {
    return { snapshot: EMPTY_SNAPSHOT, baseVersion: root?.version ?? 0 };
  }
  const snapshot = await readDocumentSnapshot(env, root.current_document_id);
  return {
    snapshot: snapshot === null ? EMPTY_SNAPSHOT : cloneSnapshotForDraft(snapshot),
    baseVersion: root.version,
  };
}

export async function startDraft(env: Env, input: StartDraftInput): Promise<StartDraftResult> {
  if (!isSnowflake(input.ownerUserId)) badRequest("invalid_owner_user_id", "owner_user_id must be a Discord snowflake");
  if (!isSnowflake(input.originGuildId)) badRequest("invalid_origin_guild_id", "origin_guild_id must be a Discord snowflake");

  if (input.targetScope === "global") {
    const homeGuildId = requireHomeGuildId(env);
    if (input.originGuildId !== homeGuildId) {
      badRequest("home_guild_required", "a global profile can only be started while acting in Bill's home guild");
    }
  } else {
    if (input.guildId === null || !isSnowflake(input.guildId)) {
      badRequest("invalid_guild_id", "guild_id must be a Discord snowflake for a server-scoped draft");
    }
    if (input.serverMode === null) {
      badRequest("invalid_server_mode", "server_mode is required for a server-scoped draft");
    }
    const homeGuildId = requireHomeGuildId(env);
    if (input.guildId === homeGuildId) {
      badRequest("server_scope_not_allowed_in_home_guild", "the home guild always uses the global profile directly");
    }
  }

  const existing = await findActiveDraftRow(env, input);
  if (existing !== null) {
    return { resumeRequired: true, draft: await buildContract(env, existing) };
  }

  const { snapshot, baseVersion } = await loadStartingSnapshot(env, input);
  const now = nowIso();
  const documentId = newId();
  const draftId = newId();

  const statements = [
    ...buildDocumentWriteStatements(env, documentId, input.ownerUserId, snapshot, now, { isNew: true, guard: null }),
    env.DB.prepare(
      `INSERT INTO profile_drafts
         (id, owner_user_id, origin_guild_id, target_scope, guild_id, server_mode, document_id,
          base_version, status, current_step, revision, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', 'orientation', 0, ?, ?)`,
    ).bind(
      draftId,
      input.ownerUserId,
      input.originGuildId,
      input.targetScope,
      input.guildId,
      input.serverMode,
      documentId,
      baseVersion,
      now,
      now,
    ),
  ];
  try {
    await env.DB.batch(statements);
  } catch {
    // Somebody else's concurrent start() won the race for the same active-draft
    // slot (the partial unique index on profile_drafts enforces this as a real
    // constraint violation, which aborts this whole batch -- including the
    // document insert -- leaving no orphaned rows behind).
    const raced = await findActiveDraftRow(env, input);
    if (raced !== null) return { resumeRequired: true, draft: await buildContract(env, raced) };
    throw new DraftError(409, "start_conflict", "Could not start a new draft; please retry");
  }

  const created = await loadOwnedDraft(env, draftId, input.ownerUserId);
  return { resumeRequired: false, draft: await buildContract(env, created) };
}

// --- step mutation -----------------------------------------------------------------------------

function normalizeSnapshotForOrientation(snapshot: DocumentSnapshot, orientation: Orientation): DocumentSnapshot {
  const caps = ORIENTATION_CAPABILITIES[orientation];
  return {
    ...snapshot,
    orientation,
    selections: {
      pronouns: snapshot.selections.pronouns,
      honourifics: caps.honourifics ? snapshot.selections.honourifics : [],
      submissiveLabels: caps.submissiveLabels ? snapshot.selections.submissiveLabels : [],
    },
    aliases: caps.aliases ? snapshot.aliases : [],
    publicSendStats: caps.stats ? snapshot.publicSendStats : false,
    throneCreatorId: caps.throne ? snapshot.throneCreatorId : null,
    links: caps.payment ? snapshot.links : snapshot.links.filter((link) => link.linkType !== "payment"),
    preferredPaymentLinkId: caps.payment ? snapshot.preferredPaymentLinkId : null,
  };
}

export interface ApplyStepInput {
  readonly draftId: string;
  readonly stepKey: StepKey;
  readonly ownerUserId: string;
  readonly expectedRevision: number;
  readonly body: unknown;
}

async function computeNewSnapshot(
  env: Env,
  draft: DraftRow,
  current: DocumentSnapshot,
  stepKey: StepKey,
  governingOrientation: Orientation | null,
  body: unknown,
  completeStep: boolean,
  dmStatusSelected: boolean,
): Promise<DocumentSnapshot> {
  const linked = linkedDraft(draft);

  if (stepKey === "orientation") {
    if (linked) badRequest("step_not_applicable", "a linked server profile inherits orientation from the global profile");
    const parsed = parseOrientationStep(body);
    return normalizeSnapshotForOrientation(current, parsed.orientation);
  }

  if (governingOrientation === null) {
    badRequest("orientation_required", "orientation must be chosen before this step");
  }

  if (stepKey === "identity") {
    if (linked) {
      const parsed = parseLinkedIdentityStep(body, governingOrientation);
      return {
        ...current,
        dmStatus: parsed.dmStatus,
        bio: parsed.bio,
        publicSendStats: parsed.publicSendStats,
        selections: {
          pronouns: parsed.pronouns,
          honourifics: parsed.honourifics,
          submissiveLabels: parsed.submissiveLabels,
        },
        aliases: parsed.aliases,
        overriddenFields: Array.from(parsed.overriddenFields),
        // On an overlay the column alone cannot express "inherit": it is the
        // `profile_color` override row (persisted from `overriddenFields`) that
        // distinguishes an inherited colour from a deliberately cleared one, so a
        // non-overriding body always stores NULL here (see resolver.ts).
        profileColor: parsed.profileColor,
      };
    }
    const parsed = parseIdentityStep(
      body,
      governingOrientation,
      !completeStep && !dmStatusSelected,
    );
    return {
      ...current,
      dmStatus: parsed.dmStatus,
      bio: parsed.bio,
      publicSendStats: parsed.publicSendStats,
      selections: { pronouns: parsed.pronouns, honourifics: parsed.honourifics, submissiveLabels: parsed.submissiveLabels },
      aliases: parsed.aliases,
      profileColor: parsed.profileColor,
    };
  }

  if (stepKey === "links") {
    if (linked) {
      const parsed = parseLinkedLinksStep(body, governingOrientation);
      await validateReferencedLinkIds(current, parsed.localLinks);
      const totalVisible = await countResolvedVisibleLinks(env, draft, parsed);
      if (totalVisible > LIMITS.linkMaxCount) {
        badRequest("too_many_links", `at most ${LIMITS.linkMaxCount} resolved links are allowed`);
      }
      return {
        ...current,
        links: parsed.localLinks,
        hiddenInheritedLinkIds: parsed.hiddenInheritedLinkIds,
        preferredPaymentLinkId: parsed.preferredPaymentLinkId,
      };
    }
    const parsed = parseLinkStep(body, governingOrientation);
    await validateReferencedLinkIds(current, parsed.links);
    return { ...current, links: parsed.links };
  }

  if (stepKey === "throne") {
    const parsed = parseThroneStep(body, governingOrientation);
    if (parsed.throneCreatorId !== null) {
      const owned = await env.DB.prepare("SELECT id FROM throne_creators WHERE id = ? AND owner_discord_user_id = ?")
        .bind(parsed.throneCreatorId, draft.owner_user_id)
        .first();
      if (owned === null) badRequest("throne_creator_not_owned", "that Throne creator is not owned by this user");
    }
    if (parsed.preferredPaymentLinkId !== null) {
      if (!current.links.some((link) => link.id === parsed.preferredPaymentLinkId && link.linkType === "payment")) {
        badRequest("invalid_preferred_payment_link", "preferred_payment_link_id must reference an existing payment link");
      }
    }
    return { ...current, throneCreatorId: parsed.throneCreatorId, preferredPaymentLinkId: parsed.preferredPaymentLinkId };
  }

  // "review" carries no field mutations of its own; it just gets marked completed.
  return current;
}

/** Any link `id` the caller echoes back must belong to a link this document already has. */
async function validateReferencedLinkIds(
  current: DocumentSnapshot,
  links: readonly { id: string | null }[],
): Promise<void> {
  const knownIds = new Set(current.links.map((link) => link.id).filter((id): id is string => id !== null));
  for (const link of links) {
    if (link.id !== null && !knownIds.has(link.id)) {
      badRequest("unknown_link_id", "id must reference a link already present on this document");
    }
  }
}

export async function countResolvedVisibleLinks(
  env: Env,
  draft: DraftRow,
  parsed: { localLinks: { enabled: boolean }[]; hiddenInheritedLinkIds: string[] },
): Promise<number> {
  const globalRoot = await env.DB.prepare("SELECT current_document_id FROM global_profiles WHERE owner_user_id = ?")
    .bind(draft.owner_user_id)
    .first<{ current_document_id: string }>();
  if (globalRoot === null) return parsed.localLinks.filter((link) => link.enabled).length;
  const globalSnapshot = await readDocumentSnapshot(env, globalRoot.current_document_id);
  const inheritedCount = (globalSnapshot?.links ?? []).filter(
    (link) => link.enabled && link.id !== null && !parsed.hiddenInheritedLinkIds.includes(link.id),
  ).length;
  return inheritedCount + parsed.localLinks.filter((link) => link.enabled).length;
}

export async function applyDraftStep(env: Env, input: ApplyStepInput): Promise<DraftContract> {
  const draft = await loadOwnedDraft(env, input.draftId, input.ownerUserId);
  if (draft.status !== "active") conflict("draft_not_active", "this draft has already been published or restarted");
  if (draft.revision !== input.expectedRevision) {
    conflict("stale_revision", "expected_revision does not match the draft's current revision");
  }

  const current = (await readDocumentSnapshot(env, draft.document_id)) ?? EMPTY_SNAPSHOT;
  const governingOrientation = await resolveGoverningOrientation(env, draft, current);
  const applicableSteps = stepsForDraft(draft.target_scope, draft.server_mode, governingOrientation);
  if (!applicableSteps.includes(input.stepKey)) {
    badRequest("step_not_applicable", `${input.stepKey} is not part of this draft's step sequence`);
  }
  const bodyRecord =
    typeof input.body === "object" && input.body !== null && !Array.isArray(input.body)
      ? (input.body as Record<string, unknown>)
      : null;
  const completeValue = bodyRecord?.complete;
  if (completeValue !== undefined && typeof completeValue !== "boolean") {
    badRequest("invalid_complete", "complete must be a boolean when provided");
  }
  const completeStep = completeValue !== false;
  if (!completeStep && input.stepKey !== "identity") {
    badRequest("partial_step_not_supported", "only identity supports partial draft persistence");
  }
  const selectedValue = bodyRecord?.dm_status_selected;
  if (selectedValue !== undefined && typeof selectedValue !== "boolean") {
    badRequest("invalid_dm_status_selected", "dm_status_selected must be a boolean when provided");
  }
  const dmStatusSelected = input.stepKey === "identity" && selectedValue === true;
  let dmStatusPreviouslySelected = false;
  if (input.stepKey === "identity") {
    const marker = await env.DB.prepare(
      "SELECT status FROM profile_draft_steps WHERE draft_id = ? AND step_key = ?",
    )
      .bind(draft.id, DM_STATUS_SELECTION_STEP_KEY)
      .first<{ status: "pending" | "completed" }>();
    dmStatusPreviouslySelected = marker?.status === "completed";
    if (completeStep && !dmStatusSelected && !dmStatusPreviouslySelected) {
      badRequest(
        "dm_status_selection_required",
        "choose a DM status from the menu before completing identity",
      );
    }
  }

  let newSnapshot: DocumentSnapshot;
  try {
    newSnapshot = await computeNewSnapshot(
      env,
      draft,
      current,
      input.stepKey,
      governingOrientation,
      input.body,
      completeStep,
      dmStatusSelected,
    );
  } catch (error) {
    if (error instanceof ValidationError) badRequest(error.code, error.message);
    throw error;
  }

  // A step mutation may *optionally* carry a bookmark update, so a caller that
  // knows where it is sending the owner next can persist both in the one
  // guarded batch instead of a second round trip. Omitting either key leaves
  // that column untouched (unlike the dedicated wizard-stage endpoint, where an
  // omitted substep clears it).
  let bookmark: { wizardStage: WizardStage | null | undefined; wizardSubstep: string | null | undefined };
  try {
    bookmark = parseWizardStageUpdate(bodyRecord ?? {});
  } catch (error) {
    if (error instanceof ValidationError) badRequest(error.code, error.message);
    throw error;
  }
  if (bookmark.wizardStage !== undefined && bookmark.wizardStage !== null) {
    // Validate against the sequence this draft will have *after* the mutation:
    // completing the orientation step is exactly when the sequence changes.
    const orientationAfter = linkedDraft(draft) ? governingOrientation : newSnapshot.orientation;
    const stagesAfter = wizardStagesForDraft(draft.target_scope, draft.server_mode, orientationAfter);
    if (!stagesAfter.includes(bookmark.wizardStage)) {
      badRequest("stage_not_applicable", `${bookmark.wizardStage} is not part of this draft's wizard sequence`);
    }
  }

  const now = nowIso();
  const newRevision = draft.revision + 1;
  const guard = { draftId: draft.id, expectedRevision: draft.revision };

  const statements: D1PreparedStatement[] = [
    ...buildDocumentWriteStatements(env, draft.document_id, draft.owner_user_id, newSnapshot, now, {
      isNew: false,
      guard,
    }),
  ];
  if (dmStatusSelected) {
    statements.push(env.DB.prepare(
      `INSERT INTO profile_draft_steps (draft_id, step_key, status, completed_at)
       SELECT ?, ?, 'completed', ?
       WHERE EXISTS (SELECT 1 FROM profile_drafts WHERE id = ? AND revision = ? AND status = 'active')
       ON CONFLICT (draft_id, step_key) DO UPDATE SET status = 'completed', completed_at = excluded.completed_at`,
    ).bind(draft.id, DM_STATUS_SELECTION_STEP_KEY, now, draft.id, draft.revision));
  }
  if (completeStep) {
    statements.push(env.DB.prepare(
      `INSERT INTO profile_draft_steps (draft_id, step_key, status, completed_at)
       SELECT ?, ?, 'completed', ?
       WHERE EXISTS (SELECT 1 FROM profile_drafts WHERE id = ? AND revision = ? AND status = 'active')
       ON CONFLICT (draft_id, step_key) DO UPDATE SET status = 'completed', completed_at = excluded.completed_at`,
    ).bind(draft.id, input.stepKey, now, draft.id, draft.revision));
  }
  const bookmarkAssignments: string[] = [];
  const bookmarkParams: unknown[] = [];
  if (bookmark.wizardStage !== undefined) {
    bookmarkAssignments.push("wizard_stage = ?");
    bookmarkParams.push(bookmark.wizardStage);
  }
  if (bookmark.wizardSubstep !== undefined) {
    bookmarkAssignments.push("wizard_substep = ?");
    bookmarkParams.push(bookmark.wizardSubstep);
  }
  statements.push(
    env.DB.prepare(
      `UPDATE profile_drafts
          SET revision = ?, current_step = ?, updated_at = ?${bookmarkAssignments.length > 0 ? `, ${bookmarkAssignments.join(", ")}` : ""}
        WHERE id = ? AND revision = ? AND status = 'active'
          AND EXISTS (SELECT 1 FROM profile_documents WHERE id = ? AND state = 'draft')`,
    ).bind(newRevision, input.stepKey, now, ...bookmarkParams, draft.id, draft.revision, draft.document_id),
  );

  const results = await env.DB.batch(statements);
  const guardResult = results.at(-1);
  if (guardResult === undefined || guardResult.meta.changes === 0) {
    conflict("stale_revision", "expected_revision does not match the draft's current revision");
  }

  const updated = await loadOwnedDraft(env, draft.id, draft.owner_user_id);
  return buildContract(env, updated);
}

// --- restart -------------------------------------------------------------------------------

export interface RestartDraftInput {
  readonly draftId: string;
  readonly ownerUserId: string;
  readonly expectedRevision: number;
}

export async function restartDraft(env: Env, input: RestartDraftInput): Promise<DraftContract> {
  const draft = await loadOwnedDraft(env, input.draftId, input.ownerUserId);
  if (draft.status !== "active") conflict("draft_not_active", "this draft has already been published");
  if (draft.revision !== input.expectedRevision) {
    conflict("stale_revision", "expected_revision does not match the draft's current revision");
  }

  const { snapshot, baseVersion } = await loadStartingSnapshot(env, {
    ownerUserId: draft.owner_user_id,
    originGuildId: draft.origin_guild_id ?? "",
    targetScope: draft.target_scope,
    guildId: draft.guild_id,
    serverMode: draft.server_mode,
  });

  const now = nowIso();
  const newRevision = draft.revision + 1;
  const guard = { draftId: draft.id, expectedRevision: draft.revision };

  const statements: D1PreparedStatement[] = [
    env.DB.prepare(
      `DELETE FROM profile_draft_steps WHERE draft_id = ? AND EXISTS (SELECT 1 FROM profile_drafts WHERE id = ? AND revision = ? AND status = 'active')`,
    ).bind(draft.id, draft.id, draft.revision),
    ...buildDocumentWriteStatements(env, draft.document_id, draft.owner_user_id, snapshot, now, { isNew: false, guard }),
    env.DB.prepare(
      `UPDATE profile_drafts
          SET revision = ?, current_step = 'orientation', base_version = ?, updated_at = ?,
              wizard_stage = NULL, wizard_substep = NULL,
              pending_throne_token_hash = NULL, pending_throne_public_creator_id = NULL,
              pending_throne_handle = NULL, pending_throne_profile_url = NULL,
              pending_throne_expires_at = NULL
        WHERE id = ? AND revision = ? AND status = 'active'
          AND EXISTS (SELECT 1 FROM profile_documents WHERE id = ? AND state = 'draft')`,
    ).bind(newRevision, baseVersion, now, draft.id, draft.revision, draft.document_id),
  ];

  const results = await env.DB.batch(statements);
  const guardResult = results.at(-1);
  if (guardResult === undefined || guardResult.meta.changes === 0) {
    conflict("stale_revision", "expected_revision does not match the draft's current revision");
  }

  const updated = await loadOwnedDraft(env, draft.id, draft.owner_user_id);
  return buildContract(env, updated);
}

// --- wizard stage bookmark ---------------------------------------------------------------------

export interface SetWizardStageInput {
  readonly draftId: string;
  readonly ownerUserId: string;
  readonly expectedRevision: number;
  readonly stage: WizardStage;
  /** Always explicit: the route normalizes an omitted `substep` to `null` (see
   * `parseWizardStageRequest`), so navigating without naming one always clears it. */
  readonly substep: string | null;
}

/**
 * Records where the private wizard is, durably, before the bot rerenders
 * its message.
 *
 * This is a full first-class mutation, not a side note: it is
 * ownership-checked, requires the draft to still be active, compare-and-swaps
 * on `expected_revision`, and bumps the revision like every other draft
 * mutation, so a duplicate click or a second device replaying an old
 * navigation loses cleanly with `stale_revision` instead of dragging the
 * winner's wizard backwards. A zero-row UPDATE is not an error in SQLite, so
 * the CAS is verified through `meta.changes` rather than assumed.
 */
export async function setDraftWizardStage(env: Env, input: SetWizardStageInput): Promise<DraftContract> {
  const draft = await loadOwnedDraft(env, input.draftId, input.ownerUserId);
  if (draft.status !== "active") conflict("draft_not_active", "this draft has already been published or restarted");
  if (draft.revision !== input.expectedRevision) {
    conflict("stale_revision", "expected_revision does not match the draft's current revision");
  }

  const snapshot = (await readDocumentSnapshot(env, draft.document_id)) ?? EMPTY_SNAPSHOT;
  const governingOrientation = await resolveGoverningOrientation(env, draft, snapshot);
  const stages = wizardStagesForDraft(draft.target_scope, draft.server_mode, governingOrientation);
  if (!stages.includes(input.stage)) {
    badRequest("stage_not_applicable", `${input.stage} is not part of this draft's wizard sequence`);
  }

  const now = nowIso();
  const newRevision = draft.revision + 1;
  const results = await env.DB.batch([
    env.DB.prepare(
      `UPDATE profile_drafts
          SET wizard_stage = ?, wizard_substep = ?, revision = ?, updated_at = ?
        WHERE id = ? AND revision = ? AND status = 'active'`,
    ).bind(input.stage, input.substep, newRevision, now, draft.id, draft.revision),
  ]);
  const guardResult = results.at(-1);
  if (guardResult === undefined || guardResult.meta.changes === 0) {
    conflict("stale_revision", "expected_revision does not match the draft's current revision");
  }

  const updated = await loadOwnedDraft(env, draft.id, draft.owner_user_id);
  return buildContract(env, updated);
}
