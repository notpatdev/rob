import type { RouteContext } from "../router.js";
import { ok } from "../util/response.js";

export function handleHealth(_ctx: RouteContext): Response {
  return ok({ status: "ok", service: "bill-worker", time: new Date().toISOString() });
}
