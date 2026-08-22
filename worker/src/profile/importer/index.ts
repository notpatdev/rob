/**
 * Orchestrates one link-page import: SSRF-defended fetch, provider-aware
 * anchor extraction (falling back to the generic extractor when a
 * provider's markup yields nothing, and to an empty result when a page is
 * entirely JS-rendered), then normalization, tracking-parameter stripping,
 * same-host filtering, and deduplication down to a small bounded candidate
 * list the caller stores for the user to review and select from.
 */
import { LIMITS } from "../contracts.js";
import { fetchHtmlSafely, defaultImporterDeps, type ImporterDeps } from "./fetchSafely.js";
import { extractAnchors, type RawAnchor } from "./extract.js";
import { classifyProviderHost, PROVIDER_SELECTORS, type ImportProvider } from "./providers.js";
import { classifyKnownProvider } from "../linkProviders.js";
import { ImportBlockedError } from "./ssrf.js";

export { ImportBlockedError } from "./ssrf.js";
export type { ImportProvider } from "./providers.js";

const MAX_ANCHORS = 100;
const MAX_CANDIDATES = LIMITS.linkMaxCount;
const MAX_LABEL_CHARS = LIMITS.linkLabelMaxChars;

const TRACKING_PARAMS = new Set([
  "utm_source",
  "utm_medium",
  "utm_campaign",
  "utm_term",
  "utm_content",
  "fbclid",
  "gclid",
  "igshid",
  "ref",
  "si",
  "mc_cid",
  "mc_eid",
]);

export interface ImportCandidate {
  readonly platform: string;
  readonly publicLabel: string;
  readonly username: string | null;
  readonly normalizedUrl: string;
  readonly linkType: "social" | "payment";
}

export interface ImportOutcome {
  readonly provider: ImportProvider;
  readonly status: "ready" | "no_links_found";
  readonly candidates: readonly ImportCandidate[];
}

/** Falls back to the page's hostname (Title Cased, without its TLD) as a label when an anchor
 * carries no visible text -- e.g. an icon-only social button rendered as an `<svg>` child. */
function fallbackLabelFromHostname(hostname: string): string {
  const withoutWww = hostname.replace(/^www\./, "");
  const firstSegment = withoutWww.split(".")[0] ?? withoutWww;
  return firstSegment.length > 0 ? firstSegment[0]!.toUpperCase() + firstSegment.slice(1) : withoutWww;
}

function buildLabel(anchorText: string, hostname: string): string {
  const collapsed = anchorText.replace(/\s+/g, " ").trim();
  const label = collapsed.length > 0 ? collapsed : fallbackLabelFromHostname(hostname);
  return label.length > MAX_LABEL_CHARS ? label.slice(0, MAX_LABEL_CHARS) : label;
}

/** Strips known tracking query parameters and any fragment, producing the same stable URL for
 * links that only differ by campaign/referral noise so dedupe treats them as one candidate. */
function normalizeCandidateUrl(rawHref: string, baseUrl: string): URL | null {
  let resolved: URL;
  try {
    resolved = new URL(rawHref, baseUrl);
  } catch {
    return null;
  }
  if (resolved.protocol !== "https:") return null;
  for (const param of [...resolved.searchParams.keys()]) {
    if (TRACKING_PARAMS.has(param.toLowerCase())) resolved.searchParams.delete(param);
  }
  resolved.hash = "";
  return resolved;
}

function candidateFromAnchor(anchor: RawAnchor, baseUrl: string, baseHostname: string): ImportCandidate | null {
  const url = normalizeCandidateUrl(anchor.href, baseUrl);
  if (url === null) return null;
  // A link-in-bio page's value is the *external* services it points to; its own nav/footer/login
  // links (which share its hostname) are never legitimate import candidates.
  if (url.hostname.toLowerCase() === baseHostname) return null;

  const known = classifyKnownProvider(url.toString());
  const platform = known?.platform ?? url.hostname.replace(/^www\./, "");
  const linkType = known?.linkType ?? "social";

  return {
    platform,
    publicLabel: buildLabel(anchor.text, url.hostname),
    username: null,
    normalizedUrl: url.toString(),
    linkType,
  };
}

function dedupeAndCap(candidates: readonly ImportCandidate[]): ImportCandidate[] {
  const seen = new Set<string>();
  const result: ImportCandidate[] = [];
  for (const candidate of candidates) {
    if (seen.has(candidate.normalizedUrl)) continue;
    seen.add(candidate.normalizedUrl);
    result.push(candidate);
    if (result.length >= MAX_CANDIDATES) break;
  }
  return result;
}

/**
 * Runs one full link-page import: fetches `sourceUrl` under the SSRF
 * guards in `fetchSafely.ts` (which throws `ImportBlockedError` for any
 * policy violation -- the caller is expected to catch that separately from
 * "fetched fine, but had no usable links"), classifies the provider from
 * the *final* (post-redirect) hostname, extracts anchors with that
 * provider's selector (falling back to the generic selector if it matches
 * nothing), and returns a normalized, deduplicated, bounded candidate list.
 */
export async function runLinkImport(sourceUrl: string, deps: ImporterDeps = defaultImporterDeps): Promise<ImportOutcome> {
  const { html, finalUrl } = await fetchHtmlSafely(sourceUrl, deps);
  const baseHostname = new URL(finalUrl).hostname.toLowerCase().replace(/^www\./, "");
  const provider = classifyProviderHost(baseHostname);

  let anchors = await extractAnchors(html, PROVIDER_SELECTORS[provider], MAX_ANCHORS);
  if (anchors.length === 0 && provider !== "generic") {
    anchors = await extractAnchors(html, PROVIDER_SELECTORS.generic, MAX_ANCHORS);
  }

  const candidates = dedupeAndCap(
    anchors
      .map((anchor) => candidateFromAnchor(anchor, finalUrl, baseHostname))
      .filter((candidate): candidate is ImportCandidate => candidate !== null),
  );

  return { provider, status: candidates.length > 0 ? "ready" : "no_links_found", candidates };
}

/** Any SSRF/format policy violation is reported as "blocked" (the conservative default: never
 * distinguish reasons that might help an attacker probe the guard), while an ordinary fetch/parse
 * failure that carries no security implication is reported as "fetch_failed". */
export function classifyImportFailureStatus(error: ImportBlockedError): "blocked" | "fetch_failed" {
  return error.code === "fetch_failed" || error.code === "unsupported_content_type" || error.code === "response_too_large"
    ? "fetch_failed"
    : "blocked";
}
