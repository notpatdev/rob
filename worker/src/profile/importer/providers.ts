/**
 * Provider classification for the link-page importer: which extraction
 * selector to use for a source page, based on its hostname. Providers are
 * matched by exact/registrable hostname rather than string-searching the
 * whole URL, so e.g. "https://evil.example/linktr.ee" is correctly treated
 * as an unknown "generic" host rather than spoofing the Linktree adapter.
 */

export type ImportProvider = "linktree" | "allmylinks" | "beacons" | "generic";

const GENERIC_SELECTOR = "a[href]";

/** CSS selectors HTMLRewriter uses to find each provider's link elements. Each provider selector
 * is tried first; if it matches nothing (e.g. the provider changed its markup, or the page is a
 * JS-only render HTMLRewriter cannot see into), the caller falls back to `GENERIC_SELECTOR`. */
export const PROVIDER_SELECTORS: Readonly<Record<ImportProvider, string>> = {
  linktree: "a[data-testid='LinkButton']",
  allmylinks: "a.profile-link, a[data-link-id]",
  beacons: "a[data-testid='block-link'], a.link-card",
  generic: GENERIC_SELECTOR,
};

const PROVIDER_HOSTS: Readonly<Record<string, ImportProvider>> = {
  "linktr.ee": "linktree",
  "allmylinks.com": "allmylinks",
  "beacons.ai": "beacons",
};

export function classifyProviderHost(hostname: string): ImportProvider {
  const normalized = hostname.toLowerCase().replace(/^www\./, "");
  return PROVIDER_HOSTS[normalized] ?? "generic";
}
