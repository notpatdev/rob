/** Normalizes a Dom/me-provided Throne username or profile URL. */

export interface NormalizedThroneInput {
  /** Lowercase, canonical Throne username. */
  username: string;
  /** Canonical profile URL for the normalized username. */
  profileUrl: string;
}

const THRONE_HOSTS = new Set(["throne.com", "www.throne.com", "throne.gifts", "www.throne.gifts"]);

/** Conservative allow-list for Throne usernames: letters, digits, dot/dash/underscore. */
const USERNAME_PATTERN = /^[a-z0-9._-]{1,64}$/;

export function normalizeThroneInput(raw: string): NormalizedThroneInput | null {
  const trimmed = raw.trim();
  if (trimmed.length === 0) return null;

  let candidate: string | null;
  if (/^https?:\/\//i.test(trimmed)) {
    candidate = extractUsernameFromUrl(trimmed);
  } else {
    candidate = trimmed.replace(/^@/, "");
  }
  if (candidate === null) return null;

  const username = candidate.trim().toLowerCase();
  if (!USERNAME_PATTERN.test(username)) return null;

  return { username, profileUrl: `https://throne.com/${username}` };
}

function extractUsernameFromUrl(raw: string): string | null {
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }
  if (!THRONE_HOSTS.has(url.hostname.toLowerCase())) return null;

  const segments = url.pathname.split("/").filter((segment) => segment.length > 0);
  if (segments.length === 0) return null;
  // Supports both `/<username>` and `/u/<username>` style profile paths.
  if (segments[0] === "u" || segments[0] === "creator" || segments[0] === "creators") {
    return segments[1] ?? null;
  }
  return segments[0] ?? null;
}
