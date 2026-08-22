import { describe, expect, it } from "vitest";
import { callWorker, jsonRequest, seedGuild } from "./helpers";

describe("bearer authentication", () => {
  it("rejects requests with no Authorization header", async () => {
    const response = await callWorker(new Request("https://worker.test/v1/guilds/1/config"));
    expect(response.status).toBe(401);
  });

  it("rejects a malformed Authorization header", async () => {
    const response = await callWorker(
      new Request("https://worker.test/v1/guilds/1/config", {
        headers: { Authorization: "Basic dGVzdA==" },
      }),
    );
    expect(response.status).toBe(401);
  });

  it("rejects an incorrect bearer token", async () => {
    const response = await callWorker(
      new Request("https://worker.test/v1/guilds/1/config", {
        headers: { Authorization: "Bearer wrong-token" },
      }),
    );
    expect(response.status).toBe(401);
  });

  it("accepts the configured bearer token", async () => {
    await seedGuild("111111111111111111");
    const response = await callWorker(
      jsonRequest("GET", "/v1/guilds/111111111111111111/config", undefined, {
        Authorization: "Bearer test-bot-token",
      }),
    );
    expect(response.status).toBe(200);
  });

  it("never authenticates the public webhook or health routes", async () => {
    const health = await callWorker(new Request("https://worker.test/health"));
    expect(health.status).toBe(200);
  });
});
