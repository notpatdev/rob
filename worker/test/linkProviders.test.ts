import { describe, expect, it } from "vitest";
import { classifyKnownProvider } from "../src/profile/linkProviders";

describe("classifyKnownProvider", () => {
  it("classifies known social platforms", () => {
    expect(classifyKnownProvider("https://twitter.com/example")).toEqual({ platform: "twitter", linkType: "social" });
    expect(classifyKnownProvider("https://x.com/example")).toEqual({ platform: "twitter", linkType: "social" });
    expect(classifyKnownProvider("https://www.instagram.com/example")).toEqual({ platform: "instagram", linkType: "social" });
  });

  it("classifies known payment platforms", () => {
    expect(classifyKnownProvider("https://cash.app/$example")).toEqual({ platform: "cashapp", linkType: "payment" });
    expect(classifyKnownProvider("https://throne.com/example")).toEqual({ platform: "throne", linkType: "payment" });
    expect(classifyKnownProvider("https://paypal.me/example")).toEqual({ platform: "paypal", linkType: "payment" });
  });

  it("strips a leading www. before matching", () => {
    expect(classifyKnownProvider("https://www.cash.app/$example")).toEqual({ platform: "cashapp", linkType: "payment" });
  });

  it("returns null for unrecognized hosts", () => {
    expect(classifyKnownProvider("https://my-own-site.example/")).toBeNull();
  });

  it("returns null for a malformed URL", () => {
    expect(classifyKnownProvider("not a url")).toBeNull();
  });
});
