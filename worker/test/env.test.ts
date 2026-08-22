import { describe, expect, it } from "vitest";
import { HomeGuildNotConfiguredError, requireHomeGuildId, type Env } from "../src/env";

function fakeEnv(homeGuildId: string): Env {
  return {
    DB: {} as unknown as Env["DB"],
    BILL_BOT_API_TOKEN: "token",
    THRONE_PUBLIC_KEY_PEM: "",
    PUBLIC_BASE_URL: "https://usebill.dev",
    BILL_HOME_GUILD_ID: homeGuildId,
  };
}

describe("requireHomeGuildId", () => {
  it("returns the configured snowflake when valid", () => {
    expect(requireHomeGuildId(fakeEnv("123456789012345678"))).toBe("123456789012345678");
  });

  it("throws HomeGuildNotConfiguredError when empty", () => {
    expect(() => requireHomeGuildId(fakeEnv(""))).toThrow(HomeGuildNotConfiguredError);
  });

  it("throws HomeGuildNotConfiguredError when not a valid snowflake", () => {
    expect(() => requireHomeGuildId(fakeEnv("not-a-snowflake"))).toThrow(HomeGuildNotConfiguredError);
  });
});
