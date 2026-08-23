import { describe, expect, it } from "vitest";
import {
  LIMITS,
  ValidationError,
  normalizeAlias,
  parseIdentityStep,
  parseLinkStep,
  parseLinkedIdentityStep,
  parseOrientationStep,
  parseThroneStep,
  stepsForDraft,
  validateHttpsUrl,
} from "../src/profile/contracts";

describe("normalizeAlias", () => {
  it("case-folds, strips a leading @, and collapses whitespace", () => {
    expect(normalizeAlias("@Foo_Bar")).toBe("foo_bar");
    expect(normalizeAlias("  Foo   Bar  ")).toBe("foo bar");
    expect(normalizeAlias("FOO")).toBe("foo");
  });
});

describe("parseOrientationStep", () => {
  it("accepts a valid orientation", () => {
    expect(parseOrientationStep({ orientation: "switch_domme" })).toEqual({ orientation: "switch_domme" });
  });

  it("rejects an unrecognized orientation", () => {
    expect(() => parseOrientationStep({ orientation: "bogus" })).toThrow(ValidationError);
  });
});

describe("parseIdentityStep orientation gating", () => {
  it("rejects honourifics for a pure submissive orientation", () => {
    expect(() =>
      parseIdentityStep({ dm_status: "open", honourifics: ["Goddess"] }, "submissive"),
    ).toThrow(ValidationError);
  });

  it("rejects submissive_labels for a pure domme orientation", () => {
    expect(() =>
      parseIdentityStep({ dm_status: "open", submissive_labels: ["Pet"] }, "domme"),
    ).toThrow(ValidationError);
  });

  it("rejects aliases for a pure domme orientation (no attribution feature)", () => {
    expect(() => parseIdentityStep({ dm_status: "open", aliases: ["Foo"] }, "domme")).toThrow(ValidationError);
  });

  it("accepts both honourifics and submissive_labels for a switch orientation", () => {
    const result = parseIdentityStep(
      { dm_status: "open", honourifics: ["Master"], submissive_labels: ["Pet"], aliases: ["Foo"] },
      "switch_domme",
    );
    expect(result.honourifics).toEqual(["Master"]);
    expect(result.submissiveLabels).toEqual(["Pet"]);
    expect(result.aliases).toEqual(["Foo"]);
  });

  it("rejects a bio over the character limit", () => {
    const longBio = "a".repeat(LIMITS.bioMaxChars + 1);
    expect(() => parseIdentityStep({ dm_status: "open", bio: longBio }, "domme")).toThrow(ValidationError);
  });

  it("rejects more than the max number of aliases and duplicate normalized aliases", () => {
    expect(() =>
      parseIdentityStep({ dm_status: "open", aliases: ["a", "b", "c", "d"] }, "submissive"),
    ).toThrow(ValidationError);
    expect(() => parseIdentityStep({ dm_status: "open", aliases: ["Foo", "@foo"] }, "submissive")).toThrow(
      ValidationError,
    );
  });

  it("requires a recognized dm_status", () => {
    expect(() => parseIdentityStep({ dm_status: "not_a_status" }, "domme")).toThrow(ValidationError);
  });
});

describe("parseLinkedIdentityStep", () => {
  it("only stores values for fields listed in overrides", () => {
    const result = parseLinkedIdentityStep({ overrides: ["dm_status"], dm_status: "closed", bio: "ignored" }, "domme");
    expect(result.overriddenFields.has("dm_status")).toBe(true);
    expect(result.dmStatus).toBe("closed");
    expect(result.overriddenFields.has("bio")).toBe(false);
    expect(result.bio).toBeNull();
  });

  it("rejects overriding a field the governing orientation does not support", () => {
    expect(() => parseLinkedIdentityStep({ overrides: ["submissive_labels"] }, "domme")).toThrow(ValidationError);
  });

  it("rejects an explicit empty pronoun override", () => {
    expect(() =>
      parseLinkedIdentityStep({ overrides: ["pronouns"], pronouns: [] }, "domme"),
    ).toThrow(expect.objectContaining({ code: "pronouns_required" }));
  });
});

describe("parseLinkStep", () => {
  it("rejects a non-https URL", () => {
    expect(() =>
      parseLinkStep(
        { links: [{ platform: "x", public_label: "X", normalized_url: "http://example.com", link_type: "social" }] },
        "domme",
      ),
    ).toThrow(ValidationError);
  });

  it("rejects embedded credentials in a URL", () => {
    expect(() => validateHttpsUrl("https://user:pass@example.com", "url")).toThrow(ValidationError);
  });

  it("rejects duplicate normalized URLs", () => {
    expect(() =>
      parseLinkStep(
        {
          links: [
            { platform: "x", public_label: "X", normalized_url: "https://example.com/a", link_type: "social" },
            { platform: "x", public_label: "X2", normalized_url: "https://example.com/a", link_type: "social" },
          ],
        },
        "domme",
      ),
    ).toThrow(ValidationError);
  });

  it("rejects payment links for an orientation without payment capability", () => {
    expect(() =>
      parseLinkStep(
        { links: [{ platform: "cashapp", public_label: "CashApp", normalized_url: "https://cash.app/x", link_type: "payment" }] },
        "submissive",
      ),
    ).toThrow(ValidationError);
  });

  it("rejects more than the max number of links", () => {
    const links = Array.from({ length: LIMITS.linkMaxCount + 1 }, (_, i) => ({
      platform: "x",
      public_label: `L${i}`,
      normalized_url: `https://example.com/${i}`,
      link_type: "social" as const,
    }));
    expect(() => parseLinkStep({ links }, "domme")).toThrow(ValidationError);
  });

  it("preserves an echoed-back id and mints none when absent", () => {
    const result = parseLinkStep(
      {
        links: [
          { id: "existing-id", platform: "x", public_label: "X", normalized_url: "https://example.com/a", link_type: "social" },
          { platform: "y", public_label: "Y", normalized_url: "https://example.com/b", link_type: "social" },
        ],
      },
      "domme",
    );
    expect(result.links[0]?.id).toBe("existing-id");
    expect(result.links[1]?.id).toBeNull();
  });
});

describe("parseThroneStep", () => {
  it("rejects the throne step entirely for an orientation without throne capability", () => {
    expect(() => parseThroneStep({ throne_creator_id: null, preferred_payment_link_id: null }, "submissive")).toThrow(
      ValidationError,
    );
  });
});

describe("stepsForDraft", () => {
  it("omits throne for submissive, includes it for domme/switch", () => {
    expect(stepsForDraft("global", null, "submissive")).toEqual(["orientation", "identity", "links", "review"]);
    expect(stepsForDraft("global", null, "domme")).toEqual(["orientation", "identity", "links", "throne", "review"]);
    expect(stepsForDraft("server", "independent", "switch_submissive")).toEqual([
      "orientation",
      "identity",
      "links",
      "throne",
      "review",
    ]);
  });

  it("never includes orientation/throne for a linked server draft, regardless of orientation", () => {
    expect(stepsForDraft("server", "linked", "domme")).toEqual(["identity", "links", "review"]);
    expect(stepsForDraft("server", "linked", null)).toEqual(["identity", "links", "review"]);
  });
});
