import type { Env } from "../env.js";
import { constantTimeEqualHex, sha256Hex } from "./hash.js";

/**
 * Validates the `Authorization: Bearer <token>` header against
 * `env.BILL_BOT_API_TOKEN` by comparing SHA-256 digests with a
 * constant-time byte loop, never the raw secret values.
 */
export async function isAuthorizedBotRequest(request: Request, env: Env): Promise<boolean> {
  const header = request.headers.get("Authorization");
  if (!header) return false;
  const match = /^Bearer\s+(.+)$/i.exec(header);
  if (!match) return false;
  const presented = match[1] ?? "";
  const [presentedHash, expectedHash] = await Promise.all([
    sha256Hex(presented),
    sha256Hex(env.BILL_BOT_API_TOKEN),
  ]);
  return constantTimeEqualHex(presentedHash, expectedHash);
}
