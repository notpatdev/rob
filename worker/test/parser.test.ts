import { describe, expect, it } from "vitest";
import { normalizeThroneInput } from "../src/throne/normalize";
import { parseThroneEvent } from "../src/throne/parser";
import { roundHalfUpToInt } from "../src/util/money";

describe("roundHalfUpToInt", () => {
  it("rounds half up to the nearest integer", () => {
    expect(roundHalfUpToInt(1098.5)).toBe(1099);
    expect(roundHalfUpToInt(1098.4)).toBe(1098);
    expect(roundHalfUpToInt(0)).toBe(0);
    expect(roundHalfUpToInt(-1098.5)).toBe(-1099);
  });

  it("compensates for float multiplication noise", () => {
    // 19.99 * 100 === 1998.9999999999998 in IEEE-754 doubles.
    expect(roundHalfUpToInt(19.99 * 100)).toBe(1999);
  });
});

describe("normalizeThroneInput", () => {
  it("accepts a plain username", () => {
    expect(normalizeThroneInput("Alice")).toEqual({
      username: "alice",
      profileUrl: "https://throne.com/alice",
    });
  });

  it("strips a leading @", () => {
    expect(normalizeThroneInput("@Alice")).toEqual({
      username: "alice",
      profileUrl: "https://throne.com/alice",
    });
  });

  it("parses a throne.com profile URL", () => {
    expect(normalizeThroneInput("https://throne.com/Alice"))
      .toEqual({ username: "alice", profileUrl: "https://throne.com/alice" });
  });

  it("parses a throne.gifts profile URL", () => {
    expect(normalizeThroneInput("https://throne.gifts/Alice"))
      .toEqual({ username: "alice", profileUrl: "https://throne.com/alice" });
  });

  it("parses a /u/<username> style URL", () => {
    expect(normalizeThroneInput("https://throne.com/u/Alice"))
      .toEqual({ username: "alice", profileUrl: "https://throne.com/alice" });
  });

  it("rejects URLs on other domains", () => {
    expect(normalizeThroneInput("https://evil.example/alice")).toBeNull();
  });

  it("rejects empty input", () => {
    expect(normalizeThroneInput("   ")).toBeNull();
  });
});

describe("parseThroneEvent", () => {
  it("parses a top-level gift_purchased event", async () => {
    const parsed = await parseThroneEvent({
      type: "gift_purchased",
      id: "evt_1",
      amount: 1099,
      currency: "usd",
      gifter: { username: "someone", display_name: "Someone" },
      item: { name: "Coffee" },
      created_at: "2024-01-01T00:00:00Z",
    });
    expect(parsed.normalizedType).toBe("gift_purchased");
    expect(parsed.eventId).toBe("evt_1");
    expect(parsed.amountMinor).toBe(1099);
    expect(parsed.currency).toBe("USD");
    expect(parsed.senderUsername).toBe("someone");
    expect(parsed.senderDisplayName).toBe("Someone");
    expect(parsed.itemName).toBe("Coffee");
    expect(parsed.purchasedAt).toBe("2024-01-01T00:00:00.000Z");
    expect(parsed.isPrivate).toBe(false);
    expect(parsed.isAnonymous).toBe(false);
  });

  it("parses fields nested under data/payload with camelCase types", async () => {
    const parsed = await parseThroneEvent({
      eventType: "ContributionPurchased",
      data: {
        order_id: "order_1",
        amount_cents: 1098.5,
        currency_code: "eur",
        sender: { handle: "supporter1" },
        product: { title: "Sticker pack" },
      },
    });
    expect(parsed.normalizedType).toBe("contribution_purchased");
    expect(parsed.orderId).toBe("order_1");
    expect(parsed.amountMinor).toBe(1099);
    expect(parsed.currency).toBe("EUR");
    expect(parsed.senderUsername).toBe("supporter1");
    expect(parsed.itemName).toBe("Sticker pack");
  });

  it("treats data.price as an already-minor-unit amount, not dollars", async () => {
    const parsed = await parseThroneEvent({ type: "gift_purchased", data: { price: 1099 } });
    expect(parsed.amountMinor).toBe(1099);
  });

  it("treats data.amount as an already-minor-unit amount, not dollars", async () => {
    const parsed = await parseThroneEvent({ type: "gift_purchased", data: { amount: 1500 } });
    expect(parsed.amountMinor).toBe(1500);
  });

  it("rounds a decimal-string amountCents HALF_UP to the nearest integer", async () => {
    const parsed = await parseThroneEvent({ type: "gift_purchased", amountCents: "1098.5" });
    expect(parsed.amountMinor).toBe(1099);
  });

  it("prefers cents-specific fields over generic price/amount fields when both are present", async () => {
    const parsed = await parseThroneEvent({ type: "gift_purchased", amount: 500, priceCents: 250 });
    expect(parsed.amountMinor).toBe(250);
  });

  it("parses legacy/common camelCase field variants", async () => {
    const parsed = await parseThroneEvent({
      event: "gift_purchased",
      eventId: "evt_camel",
      orderId: "order_camel",
      purchasedAt: "2024-02-02T00:00:00Z",
      gifterUsername: "camelgifter",
      itemName: "Camel Case Gift",
      itemImageUrl: "https://cdn.example.com/gift.png",
      isPrivate: false,
      isAnonymous: false,
      amountCents: 750,
    });
    expect(parsed.eventId).toBe("evt_camel");
    expect(parsed.orderId).toBe("order_camel");
    expect(parsed.purchasedAt).toBe("2024-02-02T00:00:00.000Z");
    expect(parsed.senderUsername).toBe("camelgifter");
    expect(parsed.itemName).toBe("Camel Case Gift");
    expect(parsed.itemImageUrl).toBe("https://cdn.example.com/gift.png");
    expect(parsed.amountMinor).toBe(750);
  });

  it("resolves item image and name from a nested wishlistItem object", async () => {
    const parsed = await parseThroneEvent({
      type: "gift_purchased",
      wishlistItem: { name: "Wishlist Gift", imageUrl: "https://cdn.example.com/wishlist.png" },
    });
    expect(parsed.itemName).toBe("Wishlist Gift");
    expect(parsed.itemImageUrl).toBe("https://cdn.example.com/wishlist.png");
  });

  it("nulls out an item image URL that is not http(s)", async () => {
    const parsed = await parseThroneEvent({
      type: "gift_purchased",
      item: { name: "Bad Image", image_url: "javascript:alert(1)" },
    });
    expect(parsed.itemImageUrl).toBeNull();
  });

  it("normalizes currency to exactly 3 uppercase letters, falling back to USD", async () => {
    const valid = await parseThroneEvent({ type: "gift_purchased", currency: "gbp" });
    expect(valid.currency).toBe("GBP");

    const invalid = await parseThroneEvent({ type: "gift_purchased", currency: "dollars" });
    expect(invalid.currency).toBe("USD");

    const missing = await parseThroneEvent({ type: "gift_purchased" });
    expect(missing.currency).toBe("USD");
  });

  it("clamps the stored amount to a nonnegative safe integer", async () => {
    const negative = await parseThroneEvent({ type: "gift_purchased", amount: -50 });
    expect(negative.amountMinor).toBe(0);
  });

  it("marks unsupported event types with a null normalizedType", async () => {
    const parsed = await parseThroneEvent({ type: "subscription_renewed" });
    expect(parsed.normalizedType).toBeNull();
  });

  it("hides amount and sender for private events", async () => {
    const parsed = await parseThroneEvent({
      type: "gift_purchased",
      amount: 2500,
      is_private: true,
      gifter: { username: "hidden" },
    });
    expect(parsed.isPrivate).toBe(true);
    expect(parsed.amountMinor).toBe(0);
    expect(parsed.senderUsername).toBeNull();
    // The raw sender is still available internally for test-sender matching.
    expect(parsed.rawSenderUsername).toBe("hidden");
  });

  it("hides sender but keeps amount for anonymous events", async () => {
    const parsed = await parseThroneEvent({
      type: "gift_purchased",
      amount: 2500,
      is_anonymous: true,
      gifter: { username: "hidden" },
    });
    expect(parsed.isAnonymous).toBe(true);
    expect(parsed.amountMinor).toBe(2500);
    expect(parsed.senderUsername).toBeNull();
  });

  it("computes a stable fallback hash omitting an absent timestamp", async () => {
    const payload = { type: "gift_crowdfunded", amount: 500, currency: "usd", gifter: { username: "a" } };
    const first = await parseThroneEvent(payload);
    const second = await parseThroneEvent(payload);
    expect(first.eventId).toBeNull();
    expect(first.orderId).toBeNull();
    expect(first.fallbackHash).not.toBeNull();
    expect(first.fallbackHash).toBe(second.fallbackHash);

    const withTimestamp = await parseThroneEvent({ ...payload, created_at: "2024-01-01T00:00:00Z" });
    expect(withTimestamp.fallbackHash).not.toBe(first.fallbackHash);
  });

  it("does not compute a fallback hash when an event id is present", async () => {
    const parsed = await parseThroneEvent({ type: "item_purchased", id: "evt_2", amount: 1 });
    expect(parsed.fallbackHash).toBeNull();
  });

  it("detects explicit test events", async () => {
    const parsed = await parseThroneEvent({ type: "gift_purchased", test: true, amount: 1 });
    expect(parsed.isTest).toBe(true);
  });
});
