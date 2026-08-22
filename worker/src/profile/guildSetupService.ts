/**
 * Persistent, revision-checked session backing the public `/bill setup`
 * wizard: create (or resume the guild's one active session), read, choose
 * a channel, and complete -- which atomically writes the `guilds` config
 * row and bridges any already-published connected profiles in that guild
 * into the legacy registration projection.
 *
 * Educational note (session/state-machine expiry): unlike the profile
 * draft system (which never expires on its own -- a user can return to a
 * DM wizard days later), a public per-guild setup message is only useful
 * for as long as it stays valid on Discord's side, so every session
 * carries a fixed `expires_at`. There is no background job that flips
 * expired sessions over; instead, "is this session actually still usable"
 * is re-checked lazily on every read/mutation (the same principle as an
 * HTTP cookie's `Max-Age`: the check happens when the value is *used*, not
 * on a wall-clock timer), and the first caller to notice an expired
 * session persists that fact so every later caller sees it too.
 */
import type { Env } from "../env.js";
import { requireHomeGuildId } from "../env.js";
import { isSnowflake } from "../util/snowflake.js";
import { newId, nowIso } from "../util/id.js";
import {
  buildRegistrationProjectionStatements,
  collectGuildSetupRegistrationProjections,
} from "./registrationSync.js";

export class GuildSetupError extends Error {
  readonly status: number;
  readonly code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

function notFound(message = "Setup session not found"): never {
  throw new GuildSetupError(404, "setup_session_not_found", message);
}
function conflict(code: string, message: string): never {
  throw new GuildSetupError(409, code, message);
}
function badRequest(code: string, message: string): never {
  throw new GuildSetupError(400, code, message);
}
function forbidden(code: string, message: string): never {
  throw new GuildSetupError(403, code, message);
}

const DEFAULT_TTL_SECONDS = 15 * 60;

type SessionStatus = "active" | "completed" | "cancelled" | "expired";
type SessionStep = "channel" | "confirm";

interface SessionRow {
  id: string;
  guild_id: string;
  initiator_user_id: string;
  status: SessionStatus;
  current_step: SessionStep;
  selected_channel_id: string | null;
  revision: number;
  public_message_id: string | null;
  expires_at: string;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

export interface GuildSetupSessionContract {
  readonly id: string;
  readonly guildId: string;
  readonly initiatorUserId: string;
  readonly status: SessionStatus;
  readonly currentStep: SessionStep;
  readonly selectedChannelId: string | null;
  readonly revision: number;
  readonly publicMessageId: string | null;
  readonly expiresAt: string;
  readonly createdAt: string;
  readonly updatedAt: string;
  readonly completedAt: string | null;
}

function toContract(row: SessionRow): GuildSetupSessionContract {
  return {
    id: row.id,
    guildId: row.guild_id,
    initiatorUserId: row.initiator_user_id,
    status: row.status,
    currentStep: row.current_step,
    selectedChannelId: row.selected_channel_id,
    revision: row.revision,
    publicMessageId: row.public_message_id,
    expiresAt: row.expires_at,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    completedAt: row.completed_at,
  };
}

async function findActiveSession(env: Env, guildId: string): Promise<SessionRow | null> {
  return env.DB.prepare("SELECT * FROM guild_setup_sessions WHERE guild_id = ? AND status = 'active'")
    .bind(guildId)
    .first<SessionRow>();
}

/** Loads a session by id and, if it is `active` but past `expires_at`, lazily flips it to
 * `expired` (persisting that so every subsequent read/mutation sees the same terminal state)
 * before returning it. This is the single place expiry is actually enforced. */
async function loadSessionCheckingExpiry(env: Env, sessionId: string): Promise<SessionRow> {
  const row = await env.DB.prepare("SELECT * FROM guild_setup_sessions WHERE id = ?").bind(sessionId).first<SessionRow>();
  if (row === null) notFound();
  if (row.status === "active" && row.expires_at <= nowIso()) {
    const now = nowIso();
    await env.DB.prepare("UPDATE guild_setup_sessions SET status = 'expired', updated_at = ? WHERE id = ? AND status = 'active'")
      .bind(now, row.id)
      .run();
    row.status = "expired";
    row.updated_at = now;
  }
  return row;
}

export interface CreateGuildSetupSessionInput {
  readonly guildId: string;
  readonly initiatorUserId: string;
  readonly ttlSeconds?: number;
}

export interface CreateGuildSetupSessionResult {
  readonly resumeRequired: boolean;
  readonly session: GuildSetupSessionContract;
}

/** Starts a new setup session for `guildId`, or returns the guild's already-active one (mirroring
 * `profile_drafts`' "resume_required" shape) so re-running `/bill setup` never creates a second,
 * conflicting public message. */
export async function createGuildSetupSession(
  env: Env,
  input: CreateGuildSetupSessionInput,
): Promise<CreateGuildSetupSessionResult> {
  if (!isSnowflake(input.guildId)) badRequest("invalid_guild_id", "guild_id must be a Discord snowflake");
  if (!isSnowflake(input.initiatorUserId)) badRequest("invalid_initiator_user_id", "initiator_user_id must be a Discord snowflake");

  const existing = await findActiveSession(env, input.guildId);
  if (existing !== null) {
    // An existing session may itself be stale (past its expiry) -- re-check via the id path so
    // that case transparently starts a fresh session instead of "resuming" a dead one.
    const rechecked = await loadSessionCheckingExpiry(env, existing.id);
    if (rechecked.status === "active") return { resumeRequired: true, session: toContract(rechecked) };
  }

  const now = nowIso();
  const ttlSeconds = input.ttlSeconds ?? DEFAULT_TTL_SECONDS;
  const expiresAt = new Date(Date.now() + ttlSeconds * 1000).toISOString();
  const sessionId = newId();

  try {
    await env.DB.prepare(
      `INSERT INTO guild_setup_sessions
         (id, guild_id, initiator_user_id, status, current_step, revision, expires_at, created_at, updated_at)
       VALUES (?, ?, ?, 'active', 'channel', 0, ?, ?, ?)`,
    )
      .bind(sessionId, input.guildId, input.initiatorUserId, expiresAt, now, now)
      .run();
  } catch {
    // Lost the race for this guild's one-active-session slot to a concurrent create.
    const raced = await findActiveSession(env, input.guildId);
    if (raced !== null) return { resumeRequired: true, session: toContract(raced) };
    throw new GuildSetupError(409, "start_conflict", "Could not start a new setup session; please retry");
  }

  const created = await loadSessionCheckingExpiry(env, sessionId);
  return { resumeRequired: false, session: toContract(created) };
}

export async function getGuildSetupSession(env: Env, sessionId: string): Promise<GuildSetupSessionContract> {
  const row = await loadSessionCheckingExpiry(env, sessionId);
  return toContract(row);
}

function assertCallbackIdentity(row: SessionRow, guildId: string, initiatorUserId: string): void {
  if (row.guild_id !== guildId) forbidden("guild_mismatch", "this session does not belong to that guild");
  if (row.initiator_user_id !== initiatorUserId) {
    forbidden("not_initiator", "only the member who started this setup session can act on it");
  }
}

function assertActiveAndCurrent(row: SessionRow, expectedRevision: number): void {
  if (row.status !== "active") conflict("session_not_active", `this setup session is already ${row.status}`);
  if (row.revision !== expectedRevision) {
    conflict("stale_revision", "expected_revision does not match the session's current revision");
  }
}

export interface SetGuildSetupChannelInput {
  readonly sessionId: string;
  readonly guildId: string;
  readonly initiatorUserId: string;
  readonly expectedRevision: number;
  readonly channelId: string;
}

/** Records the chosen send-notifications channel and advances the session to `confirm`. Channel
 * *type* and Bill's own permissions in it are Discord-side facts the bot must verify itself
 * before calling this -- the Worker only owns session/guild-config state, not live Discord state. */
export async function setGuildSetupChannel(env: Env, input: SetGuildSetupChannelInput): Promise<GuildSetupSessionContract> {
  if (!isSnowflake(input.channelId)) badRequest("invalid_channel_id", "channel_id must be a Discord snowflake");

  const row = await loadSessionCheckingExpiry(env, input.sessionId);
  assertCallbackIdentity(row, input.guildId, input.initiatorUserId);
  assertActiveAndCurrent(row, input.expectedRevision);

  const now = nowIso();
  const newRevision = row.revision + 1;
  const result = await env.DB.prepare(
    `UPDATE guild_setup_sessions
       SET selected_channel_id = ?, current_step = 'confirm', revision = ?, updated_at = ?
     WHERE id = ? AND revision = ? AND status = 'active'`,
  )
    .bind(input.channelId, newRevision, now, row.id, row.revision)
    .run();
  if (result.meta.changes === 0) conflict("stale_revision", "expected_revision does not match the session's current revision");

  return getGuildSetupSession(env, row.id);
}

export interface CompleteGuildSetupInput {
  readonly sessionId: string;
  readonly guildId: string;
  readonly initiatorUserId: string;
  readonly expectedRevision: number;
}

export interface CompleteGuildSetupResult {
  readonly session: GuildSetupSessionContract;
  readonly sendChannelId: string;
}

/**
 * Finalizes setup: atomically upserts the guild's `guilds` config row,
 * marks the session `completed`, and materializes every non-conflicting
 * profile registration. Explicit v1 rows remain authoritative.
 */
export async function completeGuildSetupSession(env: Env, input: CompleteGuildSetupInput): Promise<CompleteGuildSetupResult> {
  const row = await loadSessionCheckingExpiry(env, input.sessionId);
  assertCallbackIdentity(row, input.guildId, input.initiatorUserId);
  assertActiveAndCurrent(row, input.expectedRevision);
  if (row.current_step !== "confirm" || row.selected_channel_id === null) {
    badRequest("channel_required", "a channel must be selected before completing setup");
  }

  const now = nowIso();
  const newRevision = row.revision + 1;
  const channelId = row.selected_channel_id;
  const registrationProjections = await collectGuildSetupRegistrationProjections(env, row.guild_id);
  const completedGuard = {
    sql: "EXISTS (SELECT 1 FROM guild_setup_sessions WHERE id = ? AND revision = ? AND status = 'completed')",
    params: [row.id, newRevision],
  };

  const statements: D1PreparedStatement[] = [
    env.DB.prepare(
      `UPDATE guild_setup_sessions
         SET status = 'completed', revision = ?, completed_at = ?, updated_at = ?
       WHERE id = ? AND revision = ? AND status = 'active'`,
    ).bind(newRevision, now, now, row.id, row.revision),
    env.DB.prepare(
      `INSERT INTO guilds (guild_id, send_channel_id, created_at, updated_at)
       VALUES (?, ?, ?, ?)
       ON CONFLICT (guild_id) DO UPDATE SET
         send_channel_id = excluded.send_channel_id,
         updated_at = excluded.updated_at
       WHERE EXISTS (SELECT 1 FROM guild_setup_sessions WHERE id = ? AND revision = ? AND status = 'completed')`,
    ).bind(row.guild_id, channelId, now, now, row.id, newRevision),
    ...buildRegistrationProjectionStatements(env, registrationProjections, completedGuard),
  ];

  const results = await env.DB.batch(statements);
  const guardResult = results[0];
  if (guardResult === undefined || guardResult.meta.changes === 0) {
    conflict("stale_revision", "expected_revision does not match the session's current revision");
  }

  const completed = await getGuildSetupSession(env, row.id);
  return { session: completed, sendChannelId: channelId };
}

/** Re-exported so route handlers can distinguish "home guild required" style checks if a future
 * caller needs it; unused today but keeps this module the single home-guild-aware entry point
 * for guild setup. */
export function isHomeGuild(env: Env, guildId: string): boolean {
  return guildId === requireHomeGuildId(env);
}
