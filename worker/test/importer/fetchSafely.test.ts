import { describe, expect, it } from "vitest";
import { fetchHtmlSafely, MAX_REDIRECTS, MAX_RESPONSE_BYTES, type ImporterDeps } from "../../src/profile/importer/fetchSafely";
import { ImportBlockedError } from "../../src/profile/importer/ssrf";

const PUBLIC_IP = "93.184.216.34";

function htmlResponse(body: string, extraHeaders?: Record<string, string>): Response {
  return new Response(body, { status: 200, headers: { "content-type": "text/html; charset=utf-8", ...extraHeaders } });
}

function redirectResponse(location: string): Response {
  return new Response(null, { status: 302, headers: { location } });
}

/** A deps object whose DNS resolver always says "public" and whose fetch is driven by a queue of
 * canned responses (or a handler function), so every SSRF-relevant behavior can be tested without
 * any real network access. */
function makeDeps(responder: (url: string) => Response | Promise<Response>): ImporterDeps {
  return {
    fetchImpl: (async (input: RequestInfo | URL) => responder(input.toString())) as typeof fetch,
    resolveIps: async () => [PUBLIC_IP],
  };
}

describe("fetchHtmlSafely", () => {
  it("fetches and returns HTML for a simple public URL", async () => {
    const deps = makeDeps(() => htmlResponse("<html><body>hi</body></html>"));
    const result = await fetchHtmlSafely("https://example.com/page", deps);
    expect(result.html).toContain("hi");
    expect(result.finalUrl).toBe("https://example.com/page");
  });

  it("follows up to MAX_REDIRECTS redirects, re-validating and re-resolving each hop", async () => {
    let calls = 0;
    const resolvedHosts: string[] = [];
    const deps: ImporterDeps = {
      fetchImpl: (async (input: RequestInfo | URL) => {
        calls++;
        const url = input.toString();
        if (url === "https://example.com/start") return redirectResponse("https://example.com/mid");
        if (url === "https://example.com/mid") return redirectResponse("https://example.com/final");
        return htmlResponse("<html>done</html>");
      }) as typeof fetch,
      resolveIps: async (hostname: string) => {
        resolvedHosts.push(hostname);
        return [PUBLIC_IP];
      },
    };
    const result = await fetchHtmlSafely("https://example.com/start", deps);
    expect(result.finalUrl).toBe("https://example.com/final");
    expect(calls).toBe(3);
    expect(resolvedHosts).toEqual(["example.com", "example.com", "example.com"]);
  });

  it("rejects once redirects exceed MAX_REDIRECTS", async () => {
    let hop = 0;
    const deps = makeDeps(() => {
      hop++;
      return redirectResponse(`https://example.com/hop${hop}`);
    });
    await expect(fetchHtmlSafely("https://example.com/start", deps)).rejects.toThrowError(
      expect.objectContaining({ code: "too_many_redirects" }),
    );
    expect(hop).toBe(MAX_REDIRECTS + 1);
  });

  it("re-validates and blocks a redirect target that resolves to a private address", async () => {
    const deps: ImporterDeps = {
      fetchImpl: (async (input: RequestInfo | URL) => {
        const url = input.toString();
        if (url === "https://public.example/") return redirectResponse("https://internal.example/");
        return htmlResponse("<html>should never get here</html>");
      }) as typeof fetch,
      resolveIps: async (hostname: string) => (hostname === "internal.example" ? ["10.0.0.5"] : [PUBLIC_IP]),
    };
    await expect(fetchHtmlSafely("https://public.example/", deps)).rejects.toThrowError(
      expect.objectContaining({ code: "blocked_destination" }),
    );
  });

  it("rejects a redirect to a non-https target", async () => {
    const deps = makeDeps((url) => (url === "https://example.com/" ? redirectResponse("http://example.com/insecure") : htmlResponse("")));
    await expect(fetchHtmlSafely("https://example.com/", deps)).rejects.toThrowError(
      expect.objectContaining({ code: "invalid_scheme" }),
    );
  });

  it("rejects a redirect response with no Location header", async () => {
    const deps = makeDeps(() => new Response(null, { status: 302 }));
    await expect(fetchHtmlSafely("https://example.com/", deps)).rejects.toThrowError(
      expect.objectContaining({ code: "invalid_redirect" }),
    );
  });

  it("rejects non-HTML content types", async () => {
    const deps = makeDeps(() => new Response("{}", { status: 200, headers: { "content-type": "application/json" } }));
    await expect(fetchHtmlSafely("https://example.com/", deps)).rejects.toThrowError(
      expect.objectContaining({ code: "unsupported_content_type" }),
    );
  });

  it("rejects a response whose Content-Length exceeds the size cap", async () => {
    const deps = makeDeps(() => htmlResponse("<html></html>", { "content-length": String(MAX_RESPONSE_BYTES + 1) }));
    await expect(fetchHtmlSafely("https://example.com/", deps)).rejects.toThrowError(
      expect.objectContaining({ code: "response_too_large" }),
    );
  });

  it("aborts a stream that exceeds the size cap even without a Content-Length header", async () => {
    const oversized = "a".repeat(MAX_RESPONSE_BYTES + 1024);
    const deps = makeDeps(() => htmlResponse(oversized));
    await expect(fetchHtmlSafely("https://example.com/", deps)).rejects.toThrowError(
      expect.objectContaining({ code: "response_too_large" }),
    );
  });

  it("rejects a non-2xx, non-redirect response status", async () => {
    const deps = makeDeps(() => new Response("nope", { status: 500 }));
    await expect(fetchHtmlSafely("https://example.com/", deps)).rejects.toThrowError(
      expect.objectContaining({ code: "fetch_failed" }),
    );
  });

  it("wraps a thrown fetch error (e.g. an aborted/timed-out request) as fetch_failed", async () => {
    const deps: ImporterDeps = {
      fetchImpl: (async () => {
        throw new Error("simulated network failure");
      }) as unknown as typeof fetch,
      resolveIps: async () => [PUBLIC_IP],
    };
    await expect(fetchHtmlSafely("https://example.com/", deps)).rejects.toThrowError(
      expect.objectContaining({ code: "fetch_failed" }),
    );
  });

  it("runs the DNS preflight before ever calling fetch, and blocks if it fails", async () => {
    let fetchCalled = false;
    const deps: ImporterDeps = {
      fetchImpl: (async () => {
        fetchCalled = true;
        return htmlResponse("<html></html>");
      }) as typeof fetch,
      resolveIps: async () => ["10.0.0.1"],
    };
    await expect(fetchHtmlSafely("https://blocked.example/", deps)).rejects.toBeInstanceOf(ImportBlockedError);
    expect(fetchCalled).toBe(false);
  });

  it("rejects the initial URL before any network call for format violations", async () => {
    let fetchCalled = false;
    const deps: ImporterDeps = {
      fetchImpl: (async () => {
        fetchCalled = true;
        return htmlResponse("");
      }) as typeof fetch,
      resolveIps: async () => [PUBLIC_IP],
    };
    await expect(fetchHtmlSafely("http://example.com/", deps)).rejects.toThrowError(
      expect.objectContaining({ code: "invalid_scheme" }),
    );
    expect(fetchCalled).toBe(false);
  });
});
