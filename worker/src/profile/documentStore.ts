/**
 * Reads and writes the normalized child rows (`profile_document_selections`,
 * `profile_aliases`, `profile_links`, `profile_document_overrides`,
 * `profile_link_visibility`) that make up one profile document's content,
 * as a single in-memory snapshot. Draft start/restart/step-mutation all
 * work in terms of this snapshot instead of poking individual tables, so
 * "replace this document's content" is always a delete-then-insert of the
 * whole child set rather than ad-hoc partial updates that could drift out
 * of sync with each other.
 */
import type { Env } from "../env.js";
import { newId } from "../util/id.js";
import { normalizeAlias, type DmStatus, type Orientation, type OverridableField } from "./contracts.js";

export interface DocumentSelections {
  readonly pronouns: string[];
  readonly honourifics: string[];
  readonly submissiveLabels: string[];
}

export interface DocumentLinkInput {
  /** Existing link id to keep stable across edits, or null to mint a new one. */
  readonly id: string | null;
  readonly platform: string;
  readonly publicLabel: string;
  readonly username: string | null;
  readonly normalizedUrl: string;
  readonly linkType: "social" | "payment";
  readonly enabled: boolean;
}

export interface DocumentSnapshot {
  readonly orientation: Orientation | null;
  readonly dmStatus: DmStatus | null;
  readonly bio: string | null;
  readonly publicSendStats: boolean;
  readonly throneCreatorId: string | null;
  readonly preferredPaymentLinkId: string | null;
  readonly selections: DocumentSelections;
  readonly aliases: string[];
  readonly links: DocumentLinkInput[];
  /** Only meaningful on a linked overlay document. */
  readonly overriddenFields: readonly OverridableField[];
  /** Only meaningful on a linked overlay document; ids of inherited global links this overlay hides. */
  readonly hiddenInheritedLinkIds: readonly string[];
  /** The document's optional accent colour (0x000000-0xFFFFFF), or `null` for "no colour" -- a
   * deliberate, valid choice on its own, not merely unset (see migration 0004). On a linked
   * overlay this is only meaningful when `overriddenFields` includes `"profile_color"`. */
  readonly profileColor: number | null;
}

export const EMPTY_SNAPSHOT: DocumentSnapshot = {
  orientation: null,
  dmStatus: null,
  bio: null,
  publicSendStats: false,
  throneCreatorId: null,
  preferredPaymentLinkId: null,
  selections: { pronouns: [], honourifics: [], submissiveLabels: [] },
  aliases: [],
  links: [],
  overriddenFields: [],
  hiddenInheritedLinkIds: [],
  profileColor: null,
};

interface DocumentScalarRow {
  orientation: Orientation | null;
  dm_status: DmStatus | null;
  bio: string | null;
  public_send_stats: number;
  throne_creator_id: string | null;
  preferred_payment_link_id: string | null;
  profile_color: number | null;
}

export async function readDocumentSnapshot(env: Env, documentId: string): Promise<DocumentSnapshot | null> {
  const doc = await env.DB.prepare(
    `SELECT orientation, dm_status, bio, public_send_stats, throne_creator_id, preferred_payment_link_id, profile_color
       FROM profile_documents WHERE id = ?`,
  )
    .bind(documentId)
    .first<DocumentScalarRow>();
  if (doc === null) return null;

  const [selectionsResult, aliasResult, linkResult, overrideResult, visibilityResult] = await Promise.all([
    env.DB.prepare("SELECT category, value FROM profile_document_selections WHERE document_id = ? ORDER BY sort_order, value")
      .bind(documentId)
      .all<{ category: string; value: string }>(),
    env.DB.prepare("SELECT display_alias FROM profile_aliases WHERE document_id = ? ORDER BY sort_order, display_alias")
      .bind(documentId)
      .all<{ display_alias: string }>(),
    env.DB.prepare(
      `SELECT id, platform, public_label, username, normalized_url, link_type, enabled
         FROM profile_links WHERE document_id = ? ORDER BY sort_order, id`,
    )
      .bind(documentId)
      .all<{
        id: string;
        platform: string;
        public_label: string;
        username: string | null;
        normalized_url: string;
        link_type: "social" | "payment";
        enabled: number;
      }>(),
    env.DB.prepare("SELECT field_name FROM profile_document_overrides WHERE document_id = ?")
      .bind(documentId)
      .all<{ field_name: string }>(),
    env.DB.prepare("SELECT inherited_link_id FROM profile_link_visibility WHERE document_id = ? AND visible = 0")
      .bind(documentId)
      .all<{ inherited_link_id: string }>(),
  ]);

  const selections: DocumentSelections = { pronouns: [], honourifics: [], submissiveLabels: [] };
  for (const row of selectionsResult.results) {
    if (row.category === "pronoun") selections.pronouns.push(row.value);
    else if (row.category === "honourific") selections.honourifics.push(row.value);
    else if (row.category === "submissive_label") selections.submissiveLabels.push(row.value);
  }

  return {
    orientation: doc.orientation,
    dmStatus: doc.dm_status,
    bio: doc.bio,
    publicSendStats: doc.public_send_stats === 1,
    throneCreatorId: doc.throne_creator_id,
    preferredPaymentLinkId: doc.preferred_payment_link_id,
    selections,
    aliases: aliasResult.results.map((row) => row.display_alias),
    links: linkResult.results.map((row) => ({
      id: row.id,
      platform: row.platform,
      publicLabel: row.public_label,
      username: row.username,
      normalizedUrl: row.normalized_url,
      linkType: row.link_type,
      enabled: row.enabled === 1,
    })),
    overriddenFields: overrideResult.results.map((row) => row.field_name) as OverridableField[],
    hiddenInheritedLinkIds: visibilityResult.results.map((row) => row.inherited_link_id),
    profileColor: doc.profile_color,
  };
}

/**
 * Only "own" links (the document's own social/payment entries) are part of
 * a snapshot's `links`; a linked overlay's *inherited* links live on the
 * global document and are never copied here (see `hiddenInheritedLinkIds`
 * for how an overlay instead marks specific inherited links unwanted).
 */
export interface DraftGuard {
  readonly draftId: string;
  readonly expectedRevision: number;
}

const REVISION_GUARD_SQL = `EXISTS (
  SELECT 1
    FROM profile_drafts d
    JOIN profile_documents p ON p.id = d.document_id
   WHERE d.id = ? AND d.revision = ? AND d.status = 'active'
     AND d.document_id = ? AND p.state = 'draft'
)`;

function guardSuffix(
  guard: DraftGuard | null,
  documentId: string,
  hasWhere: boolean,
): { sql: string; params: unknown[] } {
  if (guard === null) return { sql: "", params: [] };
  return {
    sql: `${hasWhere ? " AND " : " WHERE "}${REVISION_GUARD_SQL}`,
    params: [guard.draftId, guard.expectedRevision, documentId],
  };
}

/**
 * Builds the statements that create a brand new document row (used by
 * draft start) or overwrite an existing one's scalar fields and full child
 * set (used by draft restart and step mutation). When `guard` is supplied,
 * every statement is a no-op unless the named draft is still active at
 * exactly the caller's old `expectedRevision`. The caller places its
 * compare-and-swap last in the same D1 batch. A losing batch therefore sees
 * the winner's newer revision before any statement runs, making every write
 * a no-op; it can never mistake the winner's predictable next revision for
 * its own authority.
 */
export function buildDocumentWriteStatements(
  env: Env,
  documentId: string,
  ownerUserId: string,
  snapshot: DocumentSnapshot,
  now: string,
  options: { isNew: boolean; guard: DraftGuard | null },
): D1PreparedStatement[] {
  const { isNew, guard } = options;
  const statements: D1PreparedStatement[] = [];
  const guardFragment = guardSuffix(guard, documentId, true);

  if (isNew) {
    statements.push(
      env.DB.prepare(
        `INSERT INTO profile_documents
           (id, owner_user_id, state, orientation, dm_status, bio, public_send_stats,
            throne_creator_id, preferred_payment_link_id, profile_color, created_at, updated_at)
         VALUES (?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      ).bind(
        documentId,
        ownerUserId,
        snapshot.orientation,
        snapshot.dmStatus,
        snapshot.bio,
        snapshot.publicSendStats ? 1 : 0,
        snapshot.throneCreatorId,
        snapshot.preferredPaymentLinkId,
        snapshot.profileColor,
        now,
        now,
      ),
    );
  } else {
    statements.push(
      env.DB.prepare(
        `UPDATE profile_documents
           SET orientation = ?, dm_status = ?, bio = ?, public_send_stats = ?,
               throne_creator_id = ?, preferred_payment_link_id = ?, profile_color = ?, updated_at = ?
         WHERE id = ?${guardFragment.sql}`,
      ).bind(
        snapshot.orientation,
        snapshot.dmStatus,
        snapshot.bio,
        snapshot.publicSendStats ? 1 : 0,
        snapshot.throneCreatorId,
        snapshot.preferredPaymentLinkId,
        snapshot.profileColor,
        now,
        documentId,
        ...guardFragment.params,
      ),
    );

    // Full child-row replacement: delete-then-insert is simpler and safer
    // to reason about than diffing, and these tables are always small
    // (selections <= a handful, aliases <= 3, links <= 12).
    statements.push(
      env.DB.prepare(`DELETE FROM profile_document_selections WHERE document_id = ?${guardFragment.sql}`).bind(
        documentId,
        ...guardFragment.params,
      ),
    );
    statements.push(
      env.DB.prepare(`DELETE FROM profile_aliases WHERE document_id = ?${guardFragment.sql}`).bind(
        documentId,
        ...guardFragment.params,
      ),
    );
    statements.push(
      env.DB.prepare(`DELETE FROM profile_links WHERE document_id = ?${guardFragment.sql}`).bind(
        documentId,
        ...guardFragment.params,
      ),
    );
    statements.push(
      env.DB.prepare(`DELETE FROM profile_document_overrides WHERE document_id = ?${guardFragment.sql}`).bind(
        documentId,
        ...guardFragment.params,
      ),
    );
    statements.push(
      env.DB.prepare(`DELETE FROM profile_link_visibility WHERE document_id = ?${guardFragment.sql}`).bind(
        documentId,
        ...guardFragment.params,
      ),
    );
  }

  const insertGuardFragment = guardSuffix(guard, documentId, false);

  snapshot.selections.pronouns.forEach((value, index) => {
    statements.push(buildSelectionInsert(env, documentId, "pronoun", value, index, insertGuardFragment));
  });
  snapshot.selections.honourifics.forEach((value, index) => {
    statements.push(buildSelectionInsert(env, documentId, "honourific", value, index, insertGuardFragment));
  });
  snapshot.selections.submissiveLabels.forEach((value, index) => {
    statements.push(buildSelectionInsert(env, documentId, "submissive_label", value, index, insertGuardFragment));
  });

  snapshot.aliases.forEach((displayAlias, index) => {
    const normalized = normalizeAlias(displayAlias);
    statements.push(
      env.DB.prepare(
        `INSERT INTO profile_aliases (id, document_id, display_alias, normalized_alias, sort_order)
         SELECT ?, ?, ?, ?, ?${insertGuardFragment.sql}`,
      ).bind(newId(), documentId, displayAlias, normalized, index, ...insertGuardFragment.params),
    );
  });

  snapshot.links.forEach((link, index) => {
    statements.push(
      env.DB.prepare(
        `INSERT INTO profile_links
           (id, document_id, platform, public_label, username, normalized_url, link_type, sort_order, enabled, created_at, updated_at)
         SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?${insertGuardFragment.sql}`,
      ).bind(
        link.id ?? newId(),
        documentId,
        link.platform,
        link.publicLabel,
        link.username,
        link.normalizedUrl,
        link.linkType,
        index,
        link.enabled ? 1 : 0,
        now,
        now,
        ...insertGuardFragment.params,
      ),
    );
  });

  snapshot.overriddenFields.forEach((fieldName) => {
    statements.push(
      env.DB.prepare(`INSERT INTO profile_document_overrides (document_id, field_name) SELECT ?, ?${insertGuardFragment.sql}`).bind(
        documentId,
        fieldName,
        ...insertGuardFragment.params,
      ),
    );
  });

  snapshot.hiddenInheritedLinkIds.forEach((inheritedLinkId) => {
    statements.push(
      env.DB.prepare(
        `INSERT INTO profile_link_visibility (document_id, inherited_link_id, visible) SELECT ?, ?, 0${insertGuardFragment.sql}`,
      ).bind(documentId, inheritedLinkId, ...insertGuardFragment.params),
    );
  });

  return statements;
}

function buildSelectionInsert(
  env: Env,
  documentId: string,
  category: "pronoun" | "honourific" | "submissive_label",
  value: string,
  sortOrder: number,
  guardFragment: { sql: string; params: unknown[] },
): D1PreparedStatement {
  return env.DB.prepare(
    `INSERT INTO profile_document_selections (document_id, category, value, sort_order) SELECT ?, ?, ?, ?${guardFragment.sql}`,
  ).bind(documentId, category, value, sortOrder, ...guardFragment.params);
}
