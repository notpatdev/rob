/** Cloudflare Worker bindings for Bill. */
export interface Env {
  readonly DB: D1Database;

  /** Secret: long random token the Discord bot presents as a Bearer token. */
  readonly BILL_BOT_API_TOKEN: string;
  /** Secret: Throne's current Ed25519 webhook signing public key (PEM, SPKI or raw). */
  readonly THRONE_PUBLIC_KEY_PEM: string;

  /** Public base URL used to build webhook URLs returned to the bot, e.g. https://usebill.dev */
  readonly PUBLIC_BASE_URL: string;

  /** Optional comma-separated list of Throne usernames treated as test senders. */
  readonly THRONE_TEST_GIFTER_USERNAMES?: string;

  /** Optional tuning knobs; all are parsed with sane defaults if absent/invalid. */
  readonly NOTIFICATION_MAX_ATTEMPTS?: string;
  readonly NOTIFICATION_BACKOFF_BASE_SECONDS?: string;
  readonly NOTIFICATION_BACKOFF_MAX_SECONDS?: string;
  readonly MAX_TIMESTAMP_SKEW_SECONDS?: string;
}

function parsePositiveInt(value: string | undefined, fallback: number): number {
  if (!value) return fallback;
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

export interface ResolvedConfig {
  notificationMaxAttempts: number;
  notificationBackoffBaseSeconds: number;
  notificationBackoffMaxSeconds: number;
  maxTimestampSkewSeconds: number;
  testGifterUsernames: ReadonlySet<string>;
}

export function resolveConfig(env: Env): ResolvedConfig {
  const testGifterUsernames = new Set(
    (env.THRONE_TEST_GIFTER_USERNAMES ?? "")
      .split(",")
      .map((value) => value.trim().toLowerCase())
      .filter((value) => value.length > 0),
  );
  return {
    notificationMaxAttempts: parsePositiveInt(env.NOTIFICATION_MAX_ATTEMPTS, 5),
    notificationBackoffBaseSeconds: parsePositiveInt(env.NOTIFICATION_BACKOFF_BASE_SECONDS, 30),
    notificationBackoffMaxSeconds: parsePositiveInt(env.NOTIFICATION_BACKOFF_MAX_SECONDS, 900),
    maxTimestampSkewSeconds: parsePositiveInt(env.MAX_TIMESTAMP_SKEW_SECONDS, 300),
    testGifterUsernames,
  };
}
