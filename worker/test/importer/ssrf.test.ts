import { describe, expect, it } from "vitest";
import {
  ImportBlockedError,
  isBlockedIpAddress,
  isBlockedIpv4,
  isBlockedIpv6,
  parseIpv4,
  parseIpv6,
  preflightDns,
  validateCandidateUrl,
} from "../../src/profile/importer/ssrf";

describe("parseIpv4/parseIpv6", () => {
  it("parses valid dotted-quad IPv4 literals", () => {
    expect(parseIpv4("192.168.1.1")).toEqual([192, 168, 1, 1]);
    expect(parseIpv4("8.8.8.8")).toEqual([8, 8, 8, 8]);
  });

  it("rejects malformed IPv4-looking strings", () => {
    expect(parseIpv4("999.1.1.1")).toBeNull();
    expect(parseIpv4("example.com")).toBeNull();
    expect(parseIpv4("1.2.3")).toBeNull();
  });

  it("parses compressed and full IPv6 literals", () => {
    expect(parseIpv6("::1")).toEqual([0, 0, 0, 0, 0, 0, 0, 1]);
    expect(parseIpv6("2001:db8::1")).toEqual([0x2001, 0xdb8, 0, 0, 0, 0, 0, 1]);
    expect(parseIpv6("fe80::1")).toEqual([0xfe80, 0, 0, 0, 0, 0, 0, 1]);
  });

  it("parses IPv4-mapped IPv6 literals", () => {
    expect(parseIpv6("::ffff:169.254.169.254")).toEqual([0, 0, 0, 0, 0, 0xffff, 0xa9fe, 0xa9fe]);
  });

  it("rejects non-IPv6 strings", () => {
    expect(parseIpv6("not-an-ip")).toBeNull();
    expect(parseIpv6("192.168.1.1")).toBeNull();
  });
});

describe("isBlockedIpv4", () => {
  it("blocks loopback, private, link-local (incl. cloud metadata), and CGNAT ranges", () => {
    expect(isBlockedIpv4([127, 0, 0, 1])).toBe(true);
    expect(isBlockedIpv4([10, 1, 2, 3])).toBe(true);
    expect(isBlockedIpv4([172, 16, 0, 1])).toBe(true);
    expect(isBlockedIpv4([172, 31, 255, 255])).toBe(true);
    expect(isBlockedIpv4([172, 32, 0, 1])).toBe(false);
    expect(isBlockedIpv4([192, 168, 0, 1])).toBe(true);
    expect(isBlockedIpv4([169, 254, 169, 254])).toBe(true); // cloud metadata
    expect(isBlockedIpv4([100, 64, 0, 1])).toBe(true);
    expect(isBlockedIpv4([100, 128, 0, 1])).toBe(false);
  });

  it("blocks multicast, reserved, documentation, and broadcast ranges", () => {
    expect(isBlockedIpv4([224, 0, 0, 1])).toBe(true);
    expect(isBlockedIpv4([0, 0, 0, 0])).toBe(true);
    expect(isBlockedIpv4([192, 0, 2, 1])).toBe(true);
    expect(isBlockedIpv4([198, 51, 100, 1])).toBe(true);
    expect(isBlockedIpv4([203, 0, 113, 1])).toBe(true);
    expect(isBlockedIpv4([255, 255, 255, 255])).toBe(true);
  });

  it("allows ordinary public addresses", () => {
    expect(isBlockedIpv4([8, 8, 8, 8])).toBe(false);
    expect(isBlockedIpv4([93, 184, 216, 34])).toBe(false);
  });
});

describe("isBlockedIpv6", () => {
  it("blocks unspecified, loopback, unique-local, link-local, and multicast", () => {
    expect(isBlockedIpv6([0, 0, 0, 0, 0, 0, 0, 0])).toBe(true);
    expect(isBlockedIpv6([0, 0, 0, 0, 0, 0, 0, 1])).toBe(true);
    expect(isBlockedIpv6([0xfc00, 0, 0, 0, 0, 0, 0, 1])).toBe(true);
    expect(isBlockedIpv6([0xfe80, 0, 0, 0, 0, 0, 0, 1])).toBe(true);
    expect(isBlockedIpv6([0xff02, 0, 0, 0, 0, 0, 0, 1])).toBe(true);
  });

  it("blocks documentation and an IPv4-mapped address that maps to a blocked IPv4 range", () => {
    expect(isBlockedIpv6([0x2001, 0xdb8, 0, 0, 0, 0, 0, 1])).toBe(true);
    expect(isBlockedIpv6([0, 0, 0, 0, 0, 0xffff, 0xa9fe, 0xa9fe])).toBe(true); // ::ffff:169.254.169.254
  });

  it("allows an IPv4-mapped address that maps to a public IPv4 range", () => {
    expect(isBlockedIpv6([0, 0, 0, 0, 0, 0xffff, 0x0808, 0x0808])).toBe(false); // ::ffff:8.8.8.8
  });

  it("allows an ordinary public IPv6 address", () => {
    expect(isBlockedIpv6([0x2606, 0x4700, 0x4700, 0, 0, 0, 0, 0x1111])).toBe(false);
  });
});

describe("isBlockedIpAddress", () => {
  it("dispatches to the IPv4/IPv6 checkers and blocks unparsable input", () => {
    expect(isBlockedIpAddress("127.0.0.1")).toBe(true);
    expect(isBlockedIpAddress("8.8.8.8")).toBe(false);
    expect(isBlockedIpAddress("::1")).toBe(true);
    expect(isBlockedIpAddress("not-an-ip-at-all")).toBe(true);
  });
});

describe("validateCandidateUrl", () => {
  it("accepts a well-formed public https URL", () => {
    const url = validateCandidateUrl("https://linktr.ee/someone");
    expect(url.hostname).toBe("linktr.ee");
  });

  it("rejects non-https schemes", () => {
    expect(() => validateCandidateUrl("http://example.com")).toThrow(ImportBlockedError);
    try {
      validateCandidateUrl("http://example.com");
    } catch (error) {
      expect((error as ImportBlockedError).code).toBe("invalid_scheme");
    }
  });

  it("rejects embedded credentials", () => {
    expect(() => validateCandidateUrl("https://user:pass@example.com")).toThrowError(
      expect.objectContaining({ code: "credentials_in_url" }),
    );
  });

  it("rejects non-default ports", () => {
    expect(() => validateCandidateUrl("https://example.com:8443/")).toThrowError(
      expect.objectContaining({ code: "unexpected_port" }),
    );
  });

  it("rejects IPv4 and IPv6 literal hosts", () => {
    expect(() => validateCandidateUrl("https://192.168.1.1/")).toThrowError(
      expect.objectContaining({ code: "ip_literal_blocked" }),
    );
    expect(() => validateCandidateUrl("https://[::1]/")).toThrowError(
      expect.objectContaining({ code: "ip_literal_blocked" }),
    );
  });

  it("rejects localhost-style and internal-looking hostnames", () => {
    for (const host of ["localhost", "foo.localhost", "printer.local", "service.internal", "metadata.google.internal"]) {
      expect(() => validateCandidateUrl(`https://${host}/`)).toThrowError(
        expect.objectContaining({ code: "blocked_destination" }),
      );
    }
  });

  it("rejects malformed URLs", () => {
    expect(() => validateCandidateUrl("not a url")).toThrowError(expect.objectContaining({ code: "invalid_url" }));
  });
});

describe("preflightDns", () => {
  it("passes when every resolved address is public", async () => {
    await expect(preflightDns("example.com", async () => ["93.184.216.34"])).resolves.toBeUndefined();
  });

  it("rejects when any resolved address is private/internal", async () => {
    await expect(preflightDns("evil.example", async () => ["93.184.216.34", "10.0.0.5"])).rejects.toThrowError(
      expect.objectContaining({ code: "blocked_destination" }),
    );
  });

  it("rejects when resolution returns no addresses at all", async () => {
    await expect(preflightDns("nowhere.example", async () => [])).rejects.toThrowError(
      expect.objectContaining({ code: "dns_resolution_failed" }),
    );
  });
});
