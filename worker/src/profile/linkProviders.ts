/**
 * Known-provider classification for links, shared by manual link entry and
 * the link-page importer.
 *
 * A link's `platform`/`link_type` are normally chosen by the caller, but
 * when a URL's hostname matches a well-known service we override both so
 * "https://cash.app/$alice" is always classified as the `cashapp` payment
 * platform even if the caller mislabeled it. Unknown hosts fall back to
 * whatever the caller explicitly selected (see `linkService.ts`), which is
 * why this classifier returns `null` rather than guessing.
 */

export interface KnownProvider {
  readonly platform: string;
  readonly linkType: "social" | "payment";
}

/** Hostnames are matched after lowercasing and stripping a leading "www.". */
const KNOWN_PROVIDERS: Readonly<Record<string, KnownProvider>> = {
  "twitter.com": { platform: "twitter", linkType: "social" },
  "x.com": { platform: "twitter", linkType: "social" },
  "instagram.com": { platform: "instagram", linkType: "social" },
  "tiktok.com": { platform: "tiktok", linkType: "social" },
  "reddit.com": { platform: "reddit", linkType: "social" },
  "onlyfans.com": { platform: "onlyfans", linkType: "social" },
  "fansly.com": { platform: "fansly", linkType: "social" },
  "discord.gg": { platform: "discord", linkType: "social" },
  "discord.com": { platform: "discord", linkType: "social" },
  "bsky.app": { platform: "bluesky", linkType: "social" },
  "youtube.com": { platform: "youtube", linkType: "social" },
  "twitch.tv": { platform: "twitch", linkType: "social" },
  "telegram.me": { platform: "telegram", linkType: "social" },
  "t.me": { platform: "telegram", linkType: "social" },
  "throne.com": { platform: "throne", linkType: "payment" },
  "throne.gifts": { platform: "throne", linkType: "payment" },
  "cash.app": { platform: "cashapp", linkType: "payment" },
  "venmo.com": { platform: "venmo", linkType: "payment" },
  "paypal.me": { platform: "paypal", linkType: "payment" },
  "paypal.com": { platform: "paypal", linkType: "payment" },
  "ko-fi.com": { platform: "kofi", linkType: "payment" },
  "patreon.com": { platform: "patreon", linkType: "payment" },
  "buymeacoffee.com": { platform: "buymeacoffee", linkType: "payment" },
  "wishtender.com": { platform: "wishtender", linkType: "payment" },
};

/** Returns the known provider for a normalized URL's hostname, or `null` for an unrecognized host. */
export function classifyKnownProvider(normalizedUrl: string): KnownProvider | null {
  let hostname: string;
  try {
    hostname = new URL(normalizedUrl).hostname.toLowerCase();
  } catch {
    return null;
  }
  hostname = hostname.replace(/^www\./, "");
  return KNOWN_PROVIDERS[hostname] ?? null;
}
