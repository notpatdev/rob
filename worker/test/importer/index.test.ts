import { describe, expect, it } from "vitest";
import { classifyImportFailureStatus, runLinkImport } from "../../src/profile/importer/index";
import { ImportBlockedError } from "../../src/profile/importer/ssrf";
import type { ImporterDeps } from "../../src/profile/importer/fetchSafely";

const PUBLIC_IP = "93.184.216.34";

function htmlResponse(body: string): Response {
  return new Response(body, { status: 200, headers: { "content-type": "text/html; charset=utf-8" } });
}

function depsFor(html: string): ImporterDeps {
  return {
    fetchImpl: (async () => htmlResponse(html)) as typeof fetch,
    resolveIps: async () => [PUBLIC_IP],
  };
}

describe("runLinkImport", () => {
  it("extracts, classifies, and normalizes candidates from a generic page", async () => {
    const html = `
      <a href="https://twitter.com/example?utm_source=linkpage">Twitter</a>
      <a href="https://cash.app/$example">Tip me</a>
      <a href="/about">About this page</a>
    `;
    const outcome = await runLinkImport("https://mypage.example/", depsFor(html));
    expect(outcome.provider).toBe("generic");
    expect(outcome.status).toBe("ready");
    expect(outcome.candidates).toEqual([
      { platform: "twitter", publicLabel: "Twitter", username: null, normalizedUrl: "https://twitter.com/example", linkType: "social" },
      { platform: "cashapp", publicLabel: "Tip me", username: null, normalizedUrl: "https://cash.app/$example", linkType: "payment" },
    ]);
  });

  it("filters out same-host links (nav/footer) and unknown-provider defaults to social", async () => {
    const html = `
      <a href="https://mypage.example/privacy">Privacy</a>
      <a href="https://unknown-service.example/u/x">My unknown service</a>
    `;
    const outcome = await runLinkImport("https://mypage.example/", depsFor(html));
    expect(outcome.candidates).toEqual([
      {
        platform: "unknown-service.example",
        publicLabel: "My unknown service",
        username: null,
        normalizedUrl: "https://unknown-service.example/u/x",
        linkType: "social",
      },
    ]);
  });

  it("uses the Linktree-specific selector when the host is linktr.ee", async () => {
    const html = `
      <a href="https://mypage.example/nav">Should be ignored (generic-only nav link)</a>
      <a data-testid="LinkButton" href="https://onlyfans.com/example">OnlyFans</a>
    `;
    const outcome = await runLinkImport("https://linktr.ee/example", depsFor(html));
    expect(outcome.provider).toBe("linktree");
    expect(outcome.candidates).toEqual([
      { platform: "onlyfans", publicLabel: "OnlyFans", username: null, normalizedUrl: "https://onlyfans.com/example", linkType: "social" },
    ]);
  });

  it("falls back to the generic selector when a provider's specific selector matches nothing", async () => {
    const html = `<a href="https://onlyfans.com/example">OnlyFans</a>`;
    const outcome = await runLinkImport("https://linktr.ee/example", depsFor(html));
    expect(outcome.provider).toBe("linktree");
    expect(outcome.candidates).toEqual([
      { platform: "onlyfans", publicLabel: "OnlyFans", username: null, normalizedUrl: "https://onlyfans.com/example", linkType: "social" },
    ]);
  });

  it("reports no_links_found for a JS-only page with no anchors at all", async () => {
    const outcome = await runLinkImport("https://mypage.example/", depsFor(`<div id="app"></div>`));
    expect(outcome.status).toBe("no_links_found");
    expect(outcome.candidates).toEqual([]);
  });

  it("deduplicates candidates that normalize to the same URL", async () => {
    const html = `
      <a href="https://twitter.com/example?utm_source=a">Twitter</a>
      <a href="https://twitter.com/example?utm_campaign=b">Twitter again</a>
    `;
    const outcome = await runLinkImport("https://mypage.example/", depsFor(html));
    expect(outcome.candidates).toHaveLength(1);
  });

  it("caps candidates at twelve even with many unique anchors", async () => {
    const html = Array.from({ length: 20 }, (_, i) => `<a href="https://service${i}.example/">Service ${i}</a>`).join("\n");
    const outcome = await runLinkImport("https://mypage.example/", depsFor(html));
    expect(outcome.candidates).toHaveLength(12);
  });

  it("propagates an ImportBlockedError for a policy-violating source URL", async () => {
    await expect(runLinkImport("http://mypage.example/", depsFor(""))).rejects.toBeInstanceOf(ImportBlockedError);
  });
});

describe("classifyImportFailureStatus", () => {
  it("maps SSRF/format policy violations to blocked", () => {
    expect(classifyImportFailureStatus(new ImportBlockedError("invalid_scheme", "x"))).toBe("blocked");
    expect(classifyImportFailureStatus(new ImportBlockedError("ip_literal_blocked", "x"))).toBe("blocked");
    expect(classifyImportFailureStatus(new ImportBlockedError("blocked_destination", "x"))).toBe("blocked");
    expect(classifyImportFailureStatus(new ImportBlockedError("too_many_redirects", "x"))).toBe("blocked");
  });

  it("maps ordinary fetch/content problems to fetch_failed", () => {
    expect(classifyImportFailureStatus(new ImportBlockedError("fetch_failed", "x"))).toBe("fetch_failed");
    expect(classifyImportFailureStatus(new ImportBlockedError("unsupported_content_type", "x"))).toBe("fetch_failed");
    expect(classifyImportFailureStatus(new ImportBlockedError("response_too_large", "x"))).toBe("fetch_failed");
  });
});
