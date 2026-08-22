import { env } from "cloudflare:test";

/** Direct D1 seeding helpers for profile-system tests, bypassing the draft/publish routes
 * so resolver and schema behavior can be tested in isolation from wizard mechanics. */

export interface SeedDocumentOptions {
  id: string;
  ownerUserId: string;
  state?: "draft" | "published" | "superseded";
  orientation?: string | null;
  dmStatus?: string | null;
  bio?: string | null;
  publicSendStats?: boolean;
  throneCreatorId?: string | null;
  preferredPaymentLinkId?: string | null;
}

export async function seedDocument(options: SeedDocumentOptions): Promise<void> {
  const now = new Date().toISOString();
  await env.DB.prepare(
    `INSERT INTO profile_documents
       (id, owner_user_id, state, orientation, dm_status, bio, public_send_stats,
        throne_creator_id, preferred_payment_link_id, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
  )
    .bind(
      options.id,
      options.ownerUserId,
      options.state ?? "published",
      options.orientation ?? null,
      options.dmStatus ?? null,
      options.bio ?? null,
      options.publicSendStats ? 1 : 0,
      options.throneCreatorId ?? null,
      options.preferredPaymentLinkId ?? null,
      now,
      now,
    )
    .run();
}

export async function seedSelection(
  documentId: string,
  category: "pronoun" | "honourific" | "submissive_label",
  value: string,
  sortOrder = 0,
): Promise<void> {
  await env.DB.prepare(
    "INSERT INTO profile_document_selections (document_id, category, value, sort_order) VALUES (?, ?, ?, ?)",
  )
    .bind(documentId, category, value, sortOrder)
    .run();
}

export async function seedAlias(documentId: string, displayAlias: string, normalizedAlias: string, sortOrder = 0): Promise<void> {
  await env.DB.prepare(
    "INSERT INTO profile_aliases (id, document_id, display_alias, normalized_alias, sort_order) VALUES (?, ?, ?, ?, ?)",
  )
    .bind(crypto.randomUUID(), documentId, displayAlias, normalizedAlias, sortOrder)
    .run();
}

export interface SeedLinkOptions {
  id: string;
  documentId: string;
  platform: string;
  publicLabel: string;
  username?: string | null;
  normalizedUrl: string;
  linkType: "social" | "payment";
  sortOrder?: number;
  enabled?: boolean;
}

export async function seedLink(options: SeedLinkOptions): Promise<void> {
  const now = new Date().toISOString();
  await env.DB.prepare(
    `INSERT INTO profile_links
       (id, document_id, platform, public_label, username, normalized_url, link_type, sort_order, enabled, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
  )
    .bind(
      options.id,
      options.documentId,
      options.platform,
      options.publicLabel,
      options.username ?? null,
      options.normalizedUrl,
      options.linkType,
      options.sortOrder ?? 0,
      options.enabled === false ? 0 : 1,
      now,
      now,
    )
    .run();
}

export async function seedOverride(documentId: string, fieldName: string): Promise<void> {
  await env.DB.prepare("INSERT INTO profile_document_overrides (document_id, field_name) VALUES (?, ?)")
    .bind(documentId, fieldName)
    .run();
}

export async function seedHiddenLink(documentId: string, inheritedLinkId: string): Promise<void> {
  await env.DB.prepare(
    "INSERT INTO profile_link_visibility (document_id, inherited_link_id, visible) VALUES (?, ?, 0)",
  )
    .bind(documentId, inheritedLinkId)
    .run();
}

export async function seedGlobalProfile(ownerUserId: string, documentId: string, version = 1): Promise<void> {
  const now = new Date().toISOString();
  await env.DB.prepare(
    `INSERT INTO global_profiles (owner_user_id, current_document_id, version, published_at, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?)`,
  )
    .bind(ownerUserId, documentId, version, now, now, now)
    .run();
}

export async function seedServerProfile(options: {
  id: string;
  guildId: string;
  ownerUserId: string;
  mode: "linked" | "independent";
  documentId: string;
  version?: number;
}): Promise<void> {
  const now = new Date().toISOString();
  await env.DB.prepare(
    `INSERT INTO server_profiles (id, guild_id, owner_user_id, mode, current_document_id, version, published_at, created_at, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
  )
    .bind(options.id, options.guildId, options.ownerUserId, options.mode, options.documentId, options.version ?? 1, now, now, now)
    .run();
}
