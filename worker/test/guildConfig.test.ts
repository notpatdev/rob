import { describe, expect, it } from "vitest";
import { authHeaders, callWorker, jsonRequest, readJson, seedGuild } from "./helpers";

describe("guild config", () => {
  it("returns 404 when a guild has not been configured", async () => {
    const response = await callWorker(
      jsonRequest("GET", "/v1/guilds/222222222222222222/config", undefined, authHeaders()),
    );
    expect(response.status).toBe(404);
  });

  it("rejects a non-snowflake guildId", async () => {
    const response = await callWorker(
      jsonRequest("GET", "/v1/guilds/not-a-number/config", undefined, authHeaders()),
    );
    expect(response.status).toBe(400);
  });

  it("creates and returns config via PUT then GET", async () => {
    const putResponse = await callWorker(
      jsonRequest(
        "PUT",
        "/v1/guilds/333333333333333333/config",
        { send_channel_id: "444444444444444444" },
        authHeaders(),
      ),
    );
    expect(putResponse.status).toBe(200);
    const putBody = await readJson<{ data: { guild_id: string; send_channel_id: string } }>(putResponse);
    expect(putBody.data).toEqual({
      guild_id: "333333333333333333",
      send_channel_id: "444444444444444444",
    });

    const getResponse = await callWorker(
      jsonRequest("GET", "/v1/guilds/333333333333333333/config", undefined, authHeaders()),
    );
    expect(getResponse.status).toBe(200);
    const getBody = await readJson<{ data: { guild_id: string; send_channel_id: string } }>(getResponse);
    expect(getBody.data.send_channel_id).toBe("444444444444444444");
  });

  it("updates the channel when reconfigured", async () => {
    await seedGuild("555555555555555555", "666666666666666666");
    const response = await callWorker(
      jsonRequest(
        "PUT",
        "/v1/guilds/555555555555555555/config",
        { send_channel_id: "777777777777777777" },
        authHeaders(),
      ),
    );
    expect(response.status).toBe(200);
    const body = await readJson<{ data: { send_channel_id: string } }>(response);
    expect(body.data.send_channel_id).toBe("777777777777777777");
  });

  it("rejects a non-snowflake send_channel_id", async () => {
    const response = await callWorker(
      jsonRequest(
        "PUT",
        "/v1/guilds/888888888888888888/config",
        { send_channel_id: "12abc" },
        authHeaders(),
      ),
    );
    expect(response.status).toBe(400);
  });

  it("rejects a body that is not JSON", async () => {
    const response = await callWorker(
      new Request("https://worker.test/v1/guilds/888888888888888888/config", {
        method: "PUT",
        headers: authHeaders(),
        body: "not json",
      }),
    );
    expect(response.status).toBe(400);
  });
});
