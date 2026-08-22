/** Deterministic decimal rounding helpers for Throne minor-unit amounts. */

/**
 * Rounds a possibly-fractional minor-unit amount (e.g. cents) to the nearest
 * integer using ROUND_HALF_UP semantics, e.g. 1098.5 => 1099, -1098.5 =>
 * -1099. A tiny epsilon compensates for binary floating point noise
 * introduced by upstream dollar-to-cents multiplication (e.g.
 * `19.99 * 100 === 1998.9999999999998`) without disturbing genuine
 * half-up rounding decisions.
 */
export function roundHalfUpToInt(value: number): number {
  if (!Number.isFinite(value)) return 0;
  const sign = value < 0 ? -1 : 1;
  const magnitude = Math.abs(value);
  return sign * Math.floor(magnitude + 0.5 + 1e-9);
}
