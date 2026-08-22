import { describe, expect, it } from "vitest";
import { extractAnchors } from "../../src/profile/importer/extract";

describe("extractAnchors", () => {
  it("extracts href and trimmed text for matching anchors", async () => {
    const html = `<html><body>
      <a href="https://twitter.com/example">  Follow me on Twitter  </a>
      <a href="https://cash.app/$example">Tip me</a>
    </body></html>`;
    const anchors = await extractAnchors(html, "a[href]", 100);
    expect(anchors).toEqual([
      { href: "https://twitter.com/example", text: "Follow me on Twitter" },
      { href: "https://cash.app/$example", text: "Tip me" },
    ]);
  });

  it("collects text from nested child elements within an anchor", async () => {
    const html = `<a href="https://instagram.com/example"><span>Insta</span><b>gram</b></a>`;
    const anchors = await extractAnchors(html, "a[href]", 100);
    expect(anchors).toEqual([{ href: "https://instagram.com/example", text: "Instagram" }]);
  });

  it("ignores anchors with no href attribute", async () => {
    const html = `<a name="anchor-only">Not a link</a><a href="https://example.com/">Real link</a>`;
    const anchors = await extractAnchors(html, "a[href]", 100);
    expect(anchors).toEqual([{ href: "https://example.com/", text: "Real link" }]);
  });

  it("only matches elements selected by a provider-specific selector", async () => {
    const html = `
      <a href="https://example.com/nav">Nav link</a>
      <a data-testid="LinkButton" href="https://example.com/real">Real link</a>
    `;
    const anchors = await extractAnchors(html, "a[data-testid='LinkButton']", 100);
    expect(anchors).toEqual([{ href: "https://example.com/real", text: "Real link" }]);
  });

  it("returns nothing for a page with no matching elements at all (JS-only render)", async () => {
    const html = `<html><body><div id="app"></div><script>renderApp()</script></body></html>`;
    const anchors = await extractAnchors(html, "a[href]", 100);
    expect(anchors).toEqual([]);
  });

  it("never executes a script tag's contents even if it looks like markup", async () => {
    const html = `<script>document.write('<a href="https://evil.example/">hi</a>')</script>`;
    const anchors = await extractAnchors(html, "a[href]", 100);
    expect(anchors).toEqual([]);
  });

  it("caps the number of extracted anchors at maxAnchors", async () => {
    const links = Array.from({ length: 10 }, (_, i) => `<a href="https://example.com/${i}">Link ${i}</a>`).join("\n");
    const anchors = await extractAnchors(links, "a[href]", 3);
    expect(anchors).toHaveLength(3);
    expect(anchors[0]?.href).toBe("https://example.com/0");
  });
});
