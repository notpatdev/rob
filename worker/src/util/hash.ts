import { bytesToHex } from "./bytes.js";

export async function sha256Hex(input: string): Promise<string> {
  const data = new TextEncoder().encode(input);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return bytesToHex(new Uint8Array(digest));
}

/** Constant-time comparison over equal-length byte sequences to avoid timing oracles. */
export function constantTimeEqual(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) {
    diff |= (a[i] as number) ^ (b[i] as number);
  }
  return diff === 0;
}

export async function constantTimeEqualHex(hexA: string, hexB: string): Promise<boolean> {
  const encoder = new TextEncoder();
  const a = encoder.encode(hexA);
  const b = encoder.encode(hexB);
  return constantTimeEqual(a, b);
}
