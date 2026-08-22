/**
 * Discord snowflakes must be validated and carried as decimal-string text.
 * They must never be parsed into JS numbers (64-bit IDs lose precision as
 * IEEE-754 doubles).
 */
const SNOWFLAKE_PATTERN = /^(0|[1-9][0-9]{0,19})$/;

export function isSnowflake(value: unknown): value is string {
  return typeof value === "string" && SNOWFLAKE_PATTERN.test(value);
}

export function assertSnowflake(value: unknown, field: string): string {
  if (!isSnowflake(value)) {
    throw new InvalidSnowflakeError(field);
  }
  return value;
}

export class InvalidSnowflakeError extends Error {
  readonly field: string;
  constructor(field: string) {
    super(`${field} must be a decimal Discord snowflake string`);
    this.field = field;
  }
}
