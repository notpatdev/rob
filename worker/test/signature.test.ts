import { describe, expect, it } from "vitest";
import { generateThroneKeyPair } from "./helpers";
import {
  importThroneEd25519PublicKey,
  isTimestampWithinSkew,
  verifyThroneSignature,
} from "../src/throne/signature";
import { bytesToBase64Url, hexToBytes } from "../src/util/bytes";

describe("importThroneEd25519PublicKey", () => {
  it("parses a PEM-encoded SPKI key", async () => {
    const { publicKeyPem } = await generateThroneKeyPair();
    const key = await importThroneEd25519PublicKey(publicKeyPem);
    expect(key).not.toBeNull();
  });

  it("returns null for garbage input", async () => {
    const key = await importThroneEd25519PublicKey("not a key");
    expect(key).toBeNull();
  });

  it("returns null for empty input", async () => {
    const key = await importThroneEd25519PublicKey("");
    expect(key).toBeNull();
  });
});

describe("verifyThroneSignature", () => {
  it("accepts a valid signature", async () => {
    const { publicKeyPem, sign } = await generateThroneKeyPair();
    const publicKey = await importThroneEd25519PublicKey(publicKeyPem);
    if (!publicKey) throw new Error("key import failed");

    const timestamp = "1700000000";
    const rawBody = JSON.stringify({ type: "gift_purchased" });
    const signatureHeader = await sign(timestamp, rawBody);

    const verified = await verifyThroneSignature({ publicKey, timestamp, rawBody, signatureHeader });
    expect(verified).toBe(true);
  });

  it("rejects a signature over a tampered body", async () => {
    const { publicKeyPem, sign } = await generateThroneKeyPair();
    const publicKey = await importThroneEd25519PublicKey(publicKeyPem);
    if (!publicKey) throw new Error("key import failed");

    const timestamp = "1700000000";
    const signatureHeader = await sign(timestamp, JSON.stringify({ type: "gift_purchased" }));

    const verified = await verifyThroneSignature({
      publicKey,
      timestamp,
      rawBody: JSON.stringify({ type: "tampered" }),
      signatureHeader,
    });
    expect(verified).toBe(false);
  });

  it("rejects a signature over a tampered timestamp", async () => {
    const { publicKeyPem, sign } = await generateThroneKeyPair();
    const publicKey = await importThroneEd25519PublicKey(publicKeyPem);
    if (!publicKey) throw new Error("key import failed");

    const rawBody = JSON.stringify({ type: "gift_purchased" });
    const signatureHeader = await sign("1700000000", rawBody);

    const verified = await verifyThroneSignature({
      publicKey,
      timestamp: "1700000001",
      rawBody,
      signatureHeader,
    });
    expect(verified).toBe(false);
  });

  it("accepts a base64url-encoded signature as well as hex", async () => {
    const { publicKeyPem, sign } = await generateThroneKeyPair();
    const publicKey = await importThroneEd25519PublicKey(publicKeyPem);
    if (!publicKey) throw new Error("key import failed");

    const timestamp = "1700000000";
    const rawBody = "hello";
    const hexSignature = await sign(timestamp, rawBody);
    const bytes = hexToBytes(hexSignature);
    if (!bytes) throw new Error("failed to decode hex signature");
    const base64Signature = bytesToBase64Url(bytes);

    const verified = await verifyThroneSignature({
      publicKey,
      timestamp,
      rawBody,
      signatureHeader: base64Signature,
    });
    expect(verified).toBe(true);
  });
});

describe("isTimestampWithinSkew", () => {
  it("accepts a current timestamp", () => {
    const now = Date.now();
    expect(isTimestampWithinSkew(String(Math.floor(now / 1000)), 300, now)).toBe(true);
  });

  it("rejects a timestamp outside the allowed skew", () => {
    const now = Date.now();
    const staleSeconds = Math.floor(now / 1000) - 3600;
    expect(isTimestampWithinSkew(String(staleSeconds), 300, now)).toBe(false);
  });

  it("rejects a non-numeric timestamp", () => {
    expect(isTimestampWithinSkew("not-a-number", 300)).toBe(false);
  });

  it("rejects malformed digit-like timestamps (decimals, scientific notation, signs)", () => {
    const now = Date.now();
    const nowSeconds = Math.floor(now / 1000);
    // These all parse fine via `Number()`, but a real timestamp header must
    // be a plain, unsigned, integer digit string -- matching the prior
    // `str.isdigit()`-based validation.
    expect(isTimestampWithinSkew(`${nowSeconds}.5`, 300, now)).toBe(false);
    expect(isTimestampWithinSkew(`${nowSeconds}e0`, 300, now)).toBe(false);
    expect(isTimestampWithinSkew(`+${nowSeconds}`, 300, now)).toBe(false);
    expect(isTimestampWithinSkew(`-${nowSeconds}`, 300, now)).toBe(false);
  });
});
