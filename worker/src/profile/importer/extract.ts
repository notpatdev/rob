/**
 * Static HTML anchor extraction using Cloudflare's `HTMLRewriter`, a
 * streaming HTML transform/parser (not a JavaScript engine): it can select
 * elements by CSS selector and read their attributes/text as the markup
 * streams past, but it never executes any `<script>` on the page. That is
 * exactly the "no JS execution" boundary the importer must not cross --
 * pages whose real links only appear after client-side JavaScript runs
 * simply extract zero anchors here and fall back to safe manual entry.
 */

export interface RawAnchor {
  readonly href: string;
  readonly text: string;
}

interface MutableAnchor {
  href: string;
  text: string;
}

/** Extracts up to `maxAnchors` `(href, text)` pairs from elements matching `selector`. */
export async function extractAnchors(html: string, selector: string, maxAnchors: number): Promise<RawAnchor[]> {
  const anchors: MutableAnchor[] = [];
  let current: MutableAnchor | null = null;

  const rewriter = new HTMLRewriter().on(selector, {
    element(element) {
      if (anchors.length >= maxAnchors) return;
      const href = element.getAttribute("href");
      if (href === null || href.trim().length === 0) return;
      current = { href, text: "" };
      anchors.push(current);
      element.onEndTag(() => {
        current = null;
      });
    },
    text(chunk) {
      if (current !== null) current.text += chunk.text;
    },
  });

  const transformed = rewriter.transform(new Response(html, { headers: { "content-type": "text/html; charset=utf-8" } }));
  // HTMLRewriter transforms lazily as the stream is read; nothing above actually runs until the
  // transformed body is consumed, so this drives the parse to completion.
  await transformed.text();

  return anchors.slice(0, maxAnchors).map((anchor) => ({ href: anchor.href, text: anchor.text.trim() }));
}
