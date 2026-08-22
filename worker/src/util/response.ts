/** Consistent JSON envelopes for every Worker response. */

export interface ApiError {
  code: string;
  message: string;
}

function json(body: unknown, status: number, headers?: HeadersInit): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8", ...headers },
  });
}

export function ok(data: unknown, status = 200): Response {
  return json({ ok: true, data }, status);
}

export function fail(status: number, code: string, message: string): Response {
  return json({ ok: false, error: { code, message } satisfies ApiError }, status);
}

export const Errors = {
  notFound: (message = "Not found", code = "not_found") => fail(404, code, message),
  unauthorized: (message = "Unauthorized") => fail(401, "unauthorized", message),
  badRequest: (message: string, code = "bad_request") => fail(400, code, message),
  conflict: (message: string, code = "conflict") => fail(409, code, message),
  methodNotAllowed: (message = "Method not allowed") => fail(405, "method_not_allowed", message),
  internal: (message = "Internal error") => fail(500, "internal_error", message),
};
