import { describe, expect, it } from "vitest";
import { callWorker, readJson } from "./helpers";

describe("GET /health", () => {
  it("is public and reports ok", async () => {
    const response = await callWorker(new Request("https://worker.test/health"));
    expect(response.status).toBe(200);
    const body = await readJson<{ ok: boolean; data: { status: string } }>(response);
    expect(body.ok).toBe(true);
    expect(body.data.status).toBe("ok");
  });

  it("rejects the wrong method", async () => {
    const response = await callWorker(new Request("https://worker.test/health", { method: "POST" }));
    expect(response.status).toBe(405);
  });
});

describe("unknown routes", () => {
  it("returns 404", async () => {
    const response = await callWorker(new Request("https://worker.test/nope"));
    expect(response.status).toBe(404);
  });
});
