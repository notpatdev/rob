import { sha256Hex } from "../util/hash.js";
import { roundHalfUpToInt } from "../util/money.js";

export type ThroneNormalizedType =
  | "gift_purchased"
  | "contribution_purchased"
  | "gift_crowdfunded"
  | "item_purchased";

const KNOWN_TYPES: ReadonlySet<ThroneNormalizedType> = new Set([
  "gift_purchased",
  "contribution_purchased",
  "gift_crowdfunded",
  "item_purchased",
]);

export interface ParsedThroneEvent {
  rawType: string;
  normalizedType: ThroneNormalizedType | null;
  isTest: boolean;
  /** Sender username prior to privacy redaction; used only for test-sender matching. */
  rawSenderUsername: string | null;
  eventId: string | null;
  orderId: string | null;
  fallbackHash: string | null;
  amountMinor: number;
  currency: string;
  senderUsername: string | null;
  senderDisplayName: string | null;
  isPrivate: boolean;
  isAnonymous: boolean;
  itemName: string | null;
  itemImageUrl: string | null;
  purchasedAt: string | null;
}

type JsonObject = Record<string, unknown>;

function isObject(value: unknown): value is JsonObject {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Builds the ordered list of objects to search for a field, root-first then common nesting keys. */
function scopesOf(root: JsonObject): JsonObject[] {
  const scopes: JsonObject[] = [root];
  for (const key of ["data", "payload", "event", "attributes", "properties"]) {
    const nested = root[key];
    if (isObject(nested)) scopes.push(nested);
  }
  return scopes;
}

function pickString(scopes: JsonObject[], keys: readonly string[]): string | null {
  for (const scope of scopes) {
    for (const key of keys) {
      const value = scope[key];
      if (typeof value === "string" && value.trim().length > 0) return value.trim();
      if (typeof value === "number" && Number.isFinite(value)) return String(value);
    }
  }
  return null;
}

function pickNumber(scopes: JsonObject[], keys: readonly string[]): number | null {
  for (const scope of scopes) {
    for (const key of keys) {
      const value = scope[key];
      if (typeof value === "number" && Number.isFinite(value)) return value;
      if (typeof value === "string" && value.trim() !== "" && Number.isFinite(Number(value))) {
        return Number(value);
      }
    }
  }
  return null;
}

function pickBoolean(scopes: JsonObject[], keys: readonly string[]): boolean {
  for (const scope of scopes) {
    for (const key of keys) {
      const value = scope[key];
      if (typeof value === "boolean") return value;
      if (typeof value === "string") {
        if (value.toLowerCase() === "true") return true;
        if (value.toLowerCase() === "false") return false;
      }
    }
  }
  return false;
}

function pickNestedObjects(scopes: JsonObject[], keys: readonly string[]): JsonObject[] {
  const found: JsonObject[] = [];
  for (const scope of scopes) {
    for (const key of keys) {
      const value = scope[key];
      if (isObject(value)) found.push(value);
    }
  }
  return found;
}

function normalizeTypeToken(raw: string): string {
  const withUnderscores = raw.replace(/([a-z0-9])([A-Z])/g, "$1_$2");
  return withUnderscores
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "");
}

function parseTimestamp(scopes: JsonObject[]): string | null {
  const keys = [
    "purchased_at",
    "purchasedAt",
    "created_at",
    "createdAt",
    "timestamp",
    "occurred_at",
    "event_time",
    "time",
  ];
  for (const scope of scopes) {
    for (const key of keys) {
      const value = scope[key];
      if (value === undefined || value === null) continue;
      let date: Date;
      if (typeof value === "number") {
        date = new Date(value > 1e12 ? value : value * 1000);
      } else if (typeof value === "string" && value.trim().length > 0) {
        date = new Date(value);
      } else {
        continue;
      }
      if (!Number.isNaN(date.getTime())) return date.toISOString();
    }
  }
  return null;
}

const SENDER_OBJECT_KEYS = ["gifter", "sender", "supporter", "user", "from_user", "purchaser", "donor"];
const SENDER_USERNAME_KEYS = ["username", "user_name", "handle", "name"];
const SENDER_DISPLAY_NAME_KEYS = ["display_name", "displayName", "name"];
const SENDER_USERNAME_FLAT_KEYS = [
  "sender_username",
  "senderUsername",
  "gifter_username",
  "gifterUsername",
  "supporter_username",
  "from_username",
  "sender_name",
  "senderName",
];
const SENDER_DISPLAY_NAME_FLAT_KEYS = [
  "sender_name",
  "senderName",
  "gifter_name",
  "supporter_name",
  "from_name",
  "sender_display_name",
];

const ITEM_OBJECT_KEYS = ["item", "product", "gift", "reward", "wishlist_item", "wishlistItem"];
const ITEM_NAME_KEYS = ["name", "title", "item_name"];
const ITEM_IMAGE_KEYS = ["image_url", "imageUrl", "image", "thumbnail_url", "thumbnailUrl"];
const ITEM_NAME_FLAT_KEYS = ["item_name", "itemName", "product_name", "productName", "gift_name", "giftName"];
const ITEM_IMAGE_FLAT_KEYS = [
  "item_image_url",
  "itemImageUrl",
  "item_thumbnail_url",
  "itemThumbnailUrl",
  "image_url",
];

const EVENT_ID_KEYS = ["id", "event_id", "eventId", "webhook_id"];
const ORDER_ID_KEYS = ["order_id", "orderId", "purchase_id", "transaction_id"];
// Cents-specific keys take priority: Throne's real payloads carry these as
// already-integer minor units (occasionally as decimal-like strings that
// still need HALF_UP rounding to an integer, e.g. "1098.5" -> 1099).
const CENTS_AMOUNT_KEYS = [
  "amount_cents",
  "amountCents",
  "amount_minor",
  "price_cents",
  "priceCents",
  "total_cents",
  "amount_in_cents",
];
// Generic price/amount fields are ALSO already minor units in Throne's
// payloads (not dollars) -- do not multiply these by 100.
const GENERIC_AMOUNT_MINOR_KEYS = [
  "amount",
  "total",
  "price",
  "amount_usd",
  "amountUsd",
  "price_usd",
  "priceUsd",
  "gross_amount",
  "value",
];
const CURRENCY_KEYS = ["currency", "currency_code", "curr"];
const PRIVATE_KEYS = ["is_private", "isPrivate", "private", "hide_amount", "hideAmount", "amount_hidden", "amountHidden", "hidden"];
const ANONYMOUS_KEYS = ["is_anonymous", "isAnonymous", "anonymous", "is_anon"];
const TEST_KEYS = ["is_test", "test", "test_event", "sandbox"];

/**
 * Defensively parses a Throne webhook payload. Supports `gift_purchased`,
 * `contribution_purchased`, `gift_crowdfunded`, and `item_purchased` events
 * across common top-level and nested (`data`/`payload`/`event`) field
 * layouts. Unknown event types come back with `normalizedType: null` so
 * callers can ack-and-ignore them.
 */
export async function parseThroneEvent(payload: unknown): Promise<ParsedThroneEvent> {
  const root: JsonObject = isObject(payload) ? payload : {};
  const scopes = scopesOf(root);

  const rawType =
    pickString(scopes, ["type", "event_type", "eventType", "event", "name"]) ?? "unknown";
  const normalizedToken = normalizeTypeToken(rawType);
  const normalizedType = KNOWN_TYPES.has(normalizedToken as ThroneNormalizedType)
    ? (normalizedToken as ThroneNormalizedType)
    : null;

  const isTest = pickBoolean(scopes, TEST_KEYS) || normalizedToken.includes("test");

  const eventId = pickString(scopes, EVENT_ID_KEYS);
  const orderId = pickString(scopes, ORDER_ID_KEYS);

  // Cents-specific fields take priority; generic price/amount fields are
  // ALSO already integer minor units in Throne's payloads, so neither branch
  // multiplies by 100. Both are still HALF_UP-rounded to tolerate
  // decimal-like inputs (e.g. "1098.5" -> 1099) and clamped to a nonnegative
  // safe integer for storage.
  const centsAmount = pickNumber(scopes, CENTS_AMOUNT_KEYS);
  const genericAmount = centsAmount === null ? pickNumber(scopes, GENERIC_AMOUNT_MINOR_KEYS) : null;
  const roundedAmountMinor =
    centsAmount !== null
      ? roundHalfUpToInt(centsAmount)
      : genericAmount !== null
        ? roundHalfUpToInt(genericAmount)
        : 0;
  const rawAmountMinor = Math.min(Number.MAX_SAFE_INTEGER, Math.max(0, roundedAmountMinor));

  const rawCurrency = pickString(scopes, CURRENCY_KEYS);
  const upperCurrency = rawCurrency?.toUpperCase() ?? null;
  const currency = upperCurrency && /^[A-Z]{3}$/.test(upperCurrency) ? upperCurrency : "USD";

  const senderScopes = pickNestedObjects(scopes, SENDER_OBJECT_KEYS);
  const rawSenderUsername =
    pickString(senderScopes, SENDER_USERNAME_KEYS) ?? pickString(scopes, SENDER_USERNAME_FLAT_KEYS);
  const rawSenderDisplayName =
    pickString(senderScopes, SENDER_DISPLAY_NAME_KEYS) ??
    pickString(scopes, SENDER_DISPLAY_NAME_FLAT_KEYS);

  const itemScopes = pickNestedObjects(scopes, ITEM_OBJECT_KEYS);
  const rawItemName = pickString(itemScopes, ITEM_NAME_KEYS) ?? pickString(scopes, ITEM_NAME_FLAT_KEYS);
  const candidateItemImageUrl =
    pickString(itemScopes, ITEM_IMAGE_KEYS) ?? pickString(scopes, ITEM_IMAGE_FLAT_KEYS);
  // Only ever store a well-formed http(s) URL; anything else becomes null.
  const rawItemImageUrl = candidateItemImageUrl && /^https?:\/\//i.test(candidateItemImageUrl)
    ? candidateItemImageUrl
    : null;

  const isPrivate = pickBoolean(scopes, PRIVATE_KEYS);
  const isAnonymous = pickBoolean(scopes, ANONYMOUS_KEYS);

  const purchasedAt = parseTimestamp(scopes);

  let fallbackHash: string | null = null;
  if (eventId === null && orderId === null) {
    const hashInput: JsonObject = {
      type: normalizedType ?? normalizedToken,
      amountMinor: rawAmountMinor,
      currency,
      senderUsername: rawSenderUsername,
      itemName: rawItemName,
    };
    // Omit the timestamp entirely when absent rather than substituting "now",
    // which would make every retry of a timestamp-less payload look unique.
    if (purchasedAt !== null) hashInput.purchasedAt = purchasedAt;
    fallbackHash = await sha256Hex(JSON.stringify(hashInput));
  }

  const hideSender = isPrivate || isAnonymous;

  return {
    rawType,
    normalizedType,
    isTest,
    rawSenderUsername,
    eventId,
    orderId,
    fallbackHash,
    amountMinor: isPrivate ? 0 : rawAmountMinor,
    currency,
    senderUsername: hideSender ? null : rawSenderUsername,
    senderDisplayName: hideSender ? null : rawSenderDisplayName,
    isPrivate,
    isAnonymous,
    itemName: rawItemName,
    itemImageUrl: rawItemImageUrl,
    purchasedAt,
  };
}
