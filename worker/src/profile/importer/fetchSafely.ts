/**
 * SSRF-defended HTML fetching for the link-page importer: validates and
 * DNS-preflights every hop, follows redirects manually (re-validating each
 * one) up to a fixed cap, enforces a strict timeout, and bounds the
 * response to HTML content under a fixed size.
 */
import { ImportBlockedError, preflightDns, validateCandidateUrl, resolveHostnameIpsViaDoh, type DnsResolver } from "./ssrf.js";

export const MAX_REDIRECTS = 3;
export const FETCH_TIMEOUT_MS = 5_000;
export const MAX_RESPONSE_BYTES = 512 * 1024;

export interface ImporterDeps {
  readonly fetchImpl: typeof fetch;
  readonly resolveIps: DnsResolver;
}

export const defaultImporterDeps: ImporterDeps = {
  fetchImpl: (...args: Parameters<typeof fetch>) => fetch(...args),
  resolveIps: (hostname: string) => resolveHostnameIpsViaDoh(hostname, defaultImporterDeps.fetchImpl),
};

export interface FetchedHtml {
  readonly html: string;
  readonly finalUrl: string;
}

function blocked(code: string, message: string): never {
  throw new ImportBlockedError(code, message);
}

/** Reads a response body as text, aborting once more than `MAX_RESPONSE_BYTES` have been read --
 * this bounds memory/CPU use even against a server that lies about (or omits) Content-Length. */
async function readBodyCapped(response: Response): Promise<string> {
  const body = response.body;
  if (body === null) return "";
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let received = 0;
  let text = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      received += value.byteLength;
      if (received > MAX_RESPONSE_BYTES) blocked("response_too_large", "response exceeded the size limit");
      text += decoder.decode(value, { stream: true });
    }
    text += decoder.decode();
  } finally {
    reader.releaseLock();
  }
  return text;
}

/**
 * Fetches `rawUrl` as HTML, enforcing every SSRF guard end to end: scheme/credential/port/
 * IP-literal/hostname validation and a fresh DNS preflight on the initial URL and on every
 * `redirect: "manual"` hop (up to `MAX_REDIRECTS`), a hard `FETCH_TIMEOUT_MS` deadline per hop,
 * an HTML-only content-type check, and a capped, streamed read of the response body. Never
 * executes any script found in the page; the caller only ever sees inert HTML text.
 */
export async function fetchHtmlSafely(rawUrl: string, deps: ImporterDeps = defaultImporterDeps): Promise<FetchedHtml> {
  let currentUrl = validateCandidateUrl(rawUrl);

  for (let redirectCount = 0; ; redirectCount++) {
    await preflightDns(currentUrl.hostname, deps.resolveIps);

    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
    let response: Response;
    try {
      response = await deps.fetchImpl(currentUrl.toString(), {
        redirect: "manual",
        signal: controller.signal,
        headers: { accept: "text/html" },
      });
    } catch {
      blocked("fetch_failed", "could not fetch that page");
    } finally {
      clearTimeout(timeout);
    }

    if (response.status >= 300 && response.status < 400) {
      if (redirectCount >= MAX_REDIRECTS) blocked("too_many_redirects", "too many redirects");
      const location = response.headers.get("location");
      if (!location) blocked("invalid_redirect", "redirect response had no Location header");
      let redirectTarget: URL;
      try {
        redirectTarget = new URL(location as string, currentUrl);
      } catch {
        blocked("invalid_redirect", "redirect Location header was not a valid URL");
      }
      // Re-validate the *entire* redirect target from scratch -- a same-host redirect could
      // still change scheme/port, and a cross-host redirect is exactly the "server-side open
      // redirect to an internal host" case this whole module exists to stop.
      currentUrl = validateCandidateUrl(redirectTarget.toString());
      continue;
    }

    if (!response.ok) blocked("fetch_failed", `unexpected response status ${response.status}`);

    const contentType = response.headers.get("content-type") ?? "";
    if (!/text\/html/i.test(contentType)) blocked("unsupported_content_type", "response was not HTML");

    const contentLengthHeader = response.headers.get("content-length");
    if (contentLengthHeader !== null && Number(contentLengthHeader) > MAX_RESPONSE_BYTES) {
      blocked("response_too_large", "response exceeded the size limit");
    }

    const html = await readBodyCapped(response);
    return { html, finalUrl: currentUrl.toString() };
  }
}
