import { base64ToBytes, decodeSignatureBytes } from "../util/bytes.js";

/**
 * Parses a PEM (SPKI) or bare base64 Ed25519 public key into a CryptoKey.
 * Robust to header/footer whitespace variance and to keys supplied as a
 * raw 32-byte public key instead of a full SPKI wrapper.
 */
export async function importThroneEd25519PublicKey(pem: string): Promise<CryptoKey | null> {
  const stripped = pem
    .replace(/-----BEGIN [^-]+-----/g, "")
    .replace(/-----END [^-]+-----/g, "")
    .replace(/\s+/g, "");
  if (stripped.length === 0) return null;

  const bytes = base64ToBytes(stripped);
  if (!bytes) return null;

  try {
    if (bytes.length === 32) {
      // Bare 32-byte raw Ed25519 public key.
      return await crypto.subtle.importKey("raw", bytes, { name: "Ed25519" }, false, ["verify"]);
    }
    // Otherwise assume a standard SPKI-wrapped key (typically 44 bytes for Ed25519).
    return await crypto.subtle.importKey("spki", bytes, { name: "Ed25519" }, false, ["verify"]);
  } catch {
    return null;
  }
}

export interface SignatureVerificationInput {
  publicKey: CryptoKey;
  timestamp: string;
  rawBody: string;
  signatureHeader: string;
}

export async function verifyThroneSignature({
  publicKey,
  timestamp,
  rawBody,
  signatureHeader,
}: SignatureVerificationInput): Promise<boolean> {
  const signatureBytes = decodeSignatureBytes(signatureHeader);
  if (!signatureBytes) return false;

  const message = new TextEncoder().encode(`${timestamp}.${rawBody}`);
  try {
    return await crypto.subtle.verify({ name: "Ed25519" }, publicKey, signatureBytes, message);
  } catch {
    return false;
  }
}

/** True when the signed timestamp is within `maxSkewSeconds` of "now". */
// Mirrors Python's `str.isdigit()` used by the previously verified
// implementation: only ASCII digits are accepted, so signs, decimals, and
// scientific notation (e.g. "-1", "1.5", "1e10") are rejected outright
// rather than silently coerced by `Number()`.
const DIGITS_ONLY = /^[0-9]+$/;

export function isTimestampWithinSkew(
  timestampHeader: string,
  maxSkewSeconds: number,
  nowMs: number = Date.now(),
): boolean {
  const trimmed = timestampHeader.trim();
  if (!DIGITS_ONLY.test(trimmed)) return false;
  const seconds = Number(trimmed);
  if (!Number.isFinite(seconds)) return false;
  const deltaSeconds = Math.abs(nowMs / 1000 - seconds);
  return deltaSeconds <= maxSkewSeconds;
}
