/**
 * D1-backed persistence for link-page imports: create an import (runs the
 * SSRF-defended fetch and stores its candidates), then confirm it (copies
 * selected candidates into the draft's own document links atomically,
 * under the same revision compare-and-swap every draft mutation uses).
 */
import type { Env } from "../env.js";
import { newId, nowIso } from "../util/id.js";
import { LIMITS, ORIENTATION_CAPABILITIES, validateHttpsUrl } from "./contracts.js";
import { EMPTY_SNAPSHOT, buildDocumentWriteStatements, readDocumentSnapshot, type DocumentLinkInput, type DocumentSnapshot } from "./documentStore.js";
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
import { runLinkImport, classifyImportFailureStatus, ImportBlockedError, type ImportProvider } from "./importer/index.js";
import type { ImporterDeps } from "./importer/fetchSafely.js";

export interface ImportCandidateContract {
  readonly id: string;
  readonly platform: string;
  readonly publicLabel: string;
  readonly username: string | null;
  readonly normalizedUrl: string;
  readonly linkType: "social" | "payment";
  readonly selected: boolean;
}

export interface ImportContract {
  readonly id: string;
  readonly draftId: string;
  readonly sourceUrl: string;
  readonly provider: string;
  readonly status: "ready" | "no_links_found" | "fetch_failed" | "blocked";
  readonly candidates: readonly ImportCandidateContract[];
}

interface ImportRow {
  id: string;
  draft_id: string;
  source_url: string;
  provider: string;
  status: ImportContract["status"];
}

interface CandidateRow {
  id: string;
  platform: string;
  public_label: string;
  username: string | null;
  normalized_url: string;
  link_type: "social" | "payment";
  sort_order: number;
  selected: number;
}

async function loadImportContract(env: Env, importRow: ImportRow): Promise<ImportContract> {
  const { results } = await env.DB.prepare(
    `SELECT id, platform, public_label, username, normalized_url, link_type, sort_order, selected
       FROM profile_link_import_candidates WHERE import_id = ? ORDER BY sort_order, id`,
  )
    .bind(importRow.id)
    .all<CandidateRow>();

  return {
    id: importRow.id,
    draftId: importRow.draft_id,
    sourceUrl: importRow.source_url,
    provider: importRow.provider,
    status: importRow.status,
    candidates: results.map((row) => ({
      id: row.id,
      platform: row.platform,
      publicLabel: row.public_label,
      username: row.username,
      normalizedUrl: row.normalized_url,
      linkType: row.link_type,
      selected: row.selected === 1,
    })),
  };
}

async function loadOwnedImport(env: Env, draft: DraftRow, importId: string): Promise<ImportRow> {
  const row = await env.DB.prepare(
    "SELECT id, draft_id, source_url, provider, status FROM profile_link_imports WHERE id = ?",
  )
    .bind(importId)
    .first<ImportRow>();
  if (row === null || row.draft_id !== draft.id) badRequest("import_not_found", "that link import does not belong to this draft");
  return row;
}

async function assertDraftMutable(env: Env, draftId: string, ownerUserId: string, expectedRevision: number): Promise<DraftRow> {
  const draft = await loadOwnedDraft(env, draftId, ownerUserId);
  if (draft.status !== "active") conflict("draft_not_active", "this draft has already been published or restarted");
  if (draft.revision !== expectedRevision) {
    conflict("stale_revision", "expected_revision does not match the draft's current revision");
  }
  return draft;
}

export interface CreateLinkImportInput {
  readonly draftId: string;
  readonly ownerUserId: string;
  readonly expectedRevision: number;
  readonly sourceUrl: string;
}

export interface CreateLinkImportResult {
  readonly importContract: ImportContract;
  readonly draft: DraftContract;
}

/** Fetches `input.sourceUrl` under the importer's SSRF guards and stores whatever candidates
 * (possibly zero) it found. A blocked/failed fetch is not itself an application error: it is
 * recorded as a `blocked`/`fetch_failed` import with no candidates so the wizard can fall back to
 * safe manual link entry, exactly like a page that yields zero links for any other reason. */
export async function createLinkImport(
  env: Env,
  input: CreateLinkImportInput,
  deps?: ImporterDeps,
): Promise<CreateLinkImportResult> {
  const draft = await assertDraftMutable(env, input.draftId, input.ownerUserId, input.expectedRevision);
  const sourceUrl = validateHttpsUrl(input.sourceUrl, "source_url");
  const current = (await readDocumentSnapshot(env, draft.document_id)) ?? EMPTY_SNAPSHOT;
  const governingOrientation = await resolveGoverningOrientation(env, draft, current);
  if (governingOrientation === null) {
    badRequest("orientation_required", "orientation must be chosen before importing links");
  }
  const capabilities = ORIENTATION_CAPABILITIES[governingOrientation];

  const now = nowIso();
  const importId = newId();

  let provider: ImportProvider | "generic" = "generic";
  let status: ImportContract["status"];
  let candidates: { platform: string; publicLabel: string; username: string | null; normalizedUrl: string; linkType: "social" | "payment" }[] = [];

  try {
    const outcome = await runLinkImport(sourceUrl, deps);
    provider = outcome.provider;
    status = outcome.status;
    candidates = outcome.candidates.filter(
      (candidate) => candidate.linkType !== "payment" || capabilities.payment,
    );
    if (status === "ready" && candidates.length === 0) status = "no_links_found";
  } catch (error) {
    if (!(error instanceof ImportBlockedError)) throw error;
    status = classifyImportFailureStatus(error);
  }

  const newRevision = draft.revision + 1;
  const revisionGuard =
    "EXISTS (SELECT 1 FROM profile_drafts WHERE id = ? AND revision = ? AND status = 'active')";
  const statements: D1PreparedStatement[] = [
    env.DB.prepare(
      `INSERT INTO profile_link_imports (id, draft_id, source_url, provider, status, created_at, updated_at)
       SELECT ?, ?, ?, ?, ?, ?, ? WHERE ${revisionGuard}`,
    ).bind(importId, draft.id, sourceUrl, provider, status, now, now, draft.id, draft.revision),
  ];
  candidates.forEach((candidate, index) => {
    statements.push(
      env.DB.prepare(
        `INSERT INTO profile_link_import_candidates
           (id, import_id, platform, public_label, username, normalized_url, link_type, sort_order, selected)
         SELECT ?, ?, ?, ?, ?, ?, ?, ?, 1 WHERE ${revisionGuard}`,
      ).bind(
        newId(),
        importId,
        candidate.platform,
        candidate.publicLabel,
        candidate.username,
        candidate.normalizedUrl,
        candidate.linkType,
        index,
        draft.id,
        draft.revision,
      ),
    );
  });
  statements.push(
    env.DB.prepare(
      `UPDATE profile_drafts
          SET revision = ?, updated_at = ?
        WHERE id = ? AND revision = ? AND status = 'active'
          AND EXISTS (SELECT 1 FROM profile_documents WHERE id = ? AND state = 'draft')`,
    ).bind(newRevision, now, draft.id, draft.revision, draft.document_id),
  );
  const results = await env.DB.batch(statements);
  const guardResult = results.at(-1);
  if (guardResult === undefined || guardResult.meta.changes === 0) {
    conflict("stale_revision", "expected_revision does not match the draft's current revision");
  }

  const importRow: ImportRow = { id: importId, draft_id: draft.id, source_url: sourceUrl, provider, status };
  const updatedDraft = await loadOwnedDraft(env, draft.id, draft.owner_user_id);
  return {
    importContract: await loadImportContract(env, importRow),
    draft: await buildContract(env, updatedDraft),
  };
}

export interface ConfirmLinkImportInput {
  readonly draftId: string;
  readonly importId: string;
  readonly ownerUserId: string;
  readonly expectedRevision: number;
  /** Explicit candidate ids to promote; omit/null to promote every candidate still marked
   * `selected` (the default the wizard's "Looks Good!" review screen presents). */
  readonly candidateIds: readonly string[] | null;
}

export interface ConfirmLinkImportResult {
  readonly draft: DraftContract;
  readonly addedLinkCount: number;
  readonly skippedDuplicateCount: number;
}

/**
 * Promotes an import's selected candidates into the draft's own document
 * links, atomically: either every eligible candidate is appended (skipping
 * ones that would exceed the twelve-link cap or duplicate an existing
 * link's URL) and the import is cleared, or -- if the draft moved
 * underneath the caller -- nothing happens at all.
 */
export async function confirmLinkImport(env: Env, input: ConfirmLinkImportInput): Promise<ConfirmLinkImportResult> {
  const draft = await assertDraftMutable(env, input.draftId, input.ownerUserId, input.expectedRevision);
  const importRow = await loadOwnedImport(env, draft, input.importId);

  const { results: candidateRows } = await env.DB.prepare(
    `SELECT id, platform, public_label, username, normalized_url, link_type, sort_order, selected
       FROM profile_link_import_candidates WHERE import_id = ? ORDER BY sort_order, id`,
  )
    .bind(importRow.id)
    .all<CandidateRow>();

  const wanted =
    input.candidateIds === null
      ? candidateRows.filter((row) => row.selected === 1)
      : candidateRows.filter((row) => input.candidateIds!.includes(row.id));
  if (input.candidateIds !== null && wanted.length !== new Set(input.candidateIds).size) {
    badRequest("invalid_candidate_ids", "candidate_ids must reference candidates from this import");
  }

  const current = (await readDocumentSnapshot(env, draft.document_id)) ?? EMPTY_SNAPSHOT;
  const governingOrientation = await resolveGoverningOrientation(env, draft, current);
  if (governingOrientation === null) {
    badRequest("orientation_required", "orientation must be chosen before confirming imported links");
  }
  const capabilities = ORIENTATION_CAPABILITIES[governingOrientation];
  if (!capabilities.payment && wanted.some((row) => row.link_type === "payment")) {
    badRequest("payment_links_unavailable", "this orientation does not support payment links");
  }
  const existingUrls = new Set(current.links.map((link) => link.normalizedUrl));
  const newLinks: DocumentLinkInput[] = [];
  let skippedDuplicateCount = 0;

  for (const row of wanted) {
    const publicLabel = row.public_label.trim();
    if (publicLabel.length === 0 || publicLabel.length > LIMITS.linkLabelMaxChars) {
      badRequest(
        "invalid_import_candidate",
        `imported public_label must be between 1 and ${LIMITS.linkLabelMaxChars} characters`,
      );
    }
    if (row.normalized_url.length > LIMITS.linkUrlMaxChars) {
      badRequest(
        "invalid_import_candidate",
        `imported normalized_url must be at most ${LIMITS.linkUrlMaxChars} characters`,
      );
    }
    const normalizedUrl = validateHttpsUrl(row.normalized_url, "imported normalized_url");
    if (existingUrls.has(normalizedUrl)) {
      skippedDuplicateCount++;
      continue;
    }
    if (current.links.length + newLinks.length >= LIMITS.linkMaxCount) break;
    existingUrls.add(normalizedUrl);
    newLinks.push({
      id: newId(),
      platform: row.platform,
      publicLabel,
      username: row.username,
      normalizedUrl,
      linkType: row.link_type,
      enabled: true,
    });
  }

  const newSnapshot: DocumentSnapshot = { ...current, links: [...current.links, ...newLinks] };
  if (draft.target_scope === "server" && draft.server_mode === "linked") {
    const resolvedCount = await countResolvedVisibleLinks(env, draft, {
      localLinks: newSnapshot.links,
      hiddenInheritedLinkIds: [...newSnapshot.hiddenInheritedLinkIds],
    });
    if (resolvedCount > LIMITS.linkMaxCount) {
      badRequest("too_many_links", `at most ${LIMITS.linkMaxCount} resolved links are allowed`);
    }
  }

  const now = nowIso();
  const newRevision = draft.revision + 1;
  const guard = { draftId: draft.id, expectedRevision: draft.revision };

  const statements: D1PreparedStatement[] = [
    ...buildDocumentWriteStatements(env, draft.document_id, draft.owner_user_id, newSnapshot, now, { isNew: false, guard }),
    // The import and its candidates are scraped, transient staging data; once confirmed (or
    // superseded by this very confirmation), there is no reason to keep holding onto them.
    env.DB.prepare(
      `DELETE FROM profile_link_imports WHERE id = ? AND EXISTS (SELECT 1 FROM profile_drafts WHERE id = ? AND revision = ? AND status = 'active')`,
    ).bind(importRow.id, draft.id, draft.revision),
    env.DB.prepare(
      `UPDATE profile_drafts
          SET revision = ?, updated_at = ?
        WHERE id = ? AND revision = ? AND status = 'active'
          AND EXISTS (SELECT 1 FROM profile_documents WHERE id = ? AND state = 'draft')`,
    ).bind(newRevision, now, draft.id, draft.revision, draft.document_id),
  ];

  const results = await env.DB.batch(statements);
  const guardResult = results.at(-1);
  if (guardResult === undefined || guardResult.meta.changes === 0) {
    conflict("stale_revision", "expected_revision does not match the draft's current revision");
  }

  const updated = await loadOwnedDraft(env, draft.id, draft.owner_user_id);
  return { draft: await buildContract(env, updated), addedLinkCount: newLinks.length, skippedDuplicateCount };
}
