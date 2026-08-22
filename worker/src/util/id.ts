import { bytesToBase64Url } from "./bytes.js";

export function newId(): string {
  return crypto.randomUUID();
}

/** A cryptographically random URL-safe route secret, returned to the caller once. */
export function newRouteSecret(): string {
  const bytes = new Uint8Array(32);
  crypto.getRandomValues(bytes);
  return bytesToBase64Url(bytes);
}

export function newLeaseToken(): string {
  const bytes = new Uint8Array(24);
  crypto.getRandomValues(bytes);
  return bytesToBase64Url(bytes);
}

export function nowIso(): string {
  return new Date().toISOString();
}
