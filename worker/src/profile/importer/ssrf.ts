/**
 * SSRF (server-side request forgery) defenses for the link-page importer.
 *
 * Educational note: any server-side feature that fetches a URL *the caller
 * chooses* is a classic SSRF vector -- without these checks a "import my
 * link page" feature could be pointed at `http://169.254.169.254/` (a
 * cloud metadata endpoint that often hands out credentials) or at an
 * internal admin service on `10.0.0.5`, using this Worker's network
 * position as a proxy into networks the caller could never reach directly.
 * Two layers defend against this:
 *
 *  1. `validateCandidateUrl` rejects anything suspicious about the URL
 *     *text itself* (scheme, embedded credentials, IP-literal hosts,
 *     internal-looking names, non-default ports) before any network
 *     access happens at all.
 *  2. `preflightDns` then resolves the *hostname* independently and checks
 *     every returned address against known-private/internal IP ranges,
 *     because a public-looking hostname can still be configured
 *     (accidentally, or via a "DNS rebinding" attack) to resolve to a
 *     private address. This must be re-run on every redirect hop, since a
 *     legitimate first hop can still redirect to an attacker-chosen host.
 */

export class ImportBlockedError extends Error {
  readonly code: string;
  constructor(code: string, message: string) {
    super(message);
    this.code = code;
  }
}

function blocked(code: string, message: string): never {
  throw new ImportBlockedError(code, message);
}

const LOCAL_HOSTNAME_SUFFIXES = [".localhost", ".local", ".internal", ".arpa", ".home.arpa"];

function isLocalHostname(hostname: string): boolean {
  if (hostname === "localhost") return true;
  if (hostname === "metadata.google.internal") return true;
  return LOCAL_HOSTNAME_SUFFIXES.some((suffix) => hostname.endsWith(suffix));
}

const IPV4_PATTERN = /^(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})$/;

/** Parses a dotted-quad IPv4 literal, or returns `null` if `hostname` is not one. */
export function parseIpv4(hostname: string): number[] | null {
  const match = IPV4_PATTERN.exec(hostname);
  if (!match) return null;
  const octets = match.slice(1, 5).map((part) => Number.parseInt(part, 10));
  if (octets.some((octet) => Number.isNaN(octet) || octet < 0 || octet > 255)) return null;
  return octets;
}

/** Parses a (bracket-free) IPv6 literal into 8 16-bit groups, or returns `null` if it isn't one. */
export function parseIpv6(hostname: string): number[] | null {
  if (!hostname.includes(":")) return null;
  // An embedded IPv4 tail (e.g. "::ffff:169.254.169.254") is common for IPv4-mapped addresses.
  const ipv4TailMatch = /^(.*:)(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})$/.exec(hostname);
  let normalized = hostname;
  if (ipv4TailMatch) {
    const ipv4 = parseIpv4(ipv4TailMatch[2] as string);
    if (ipv4 === null) return null;
    const hi = ((ipv4[0] as number) << 8) | (ipv4[1] as number);
    const lo = ((ipv4[2] as number) << 8) | (ipv4[3] as number);
    normalized = `${ipv4TailMatch[1]}${hi.toString(16)}:${lo.toString(16)}`;
  }

  const halves = normalized.split("::");
  if (halves.length > 2) return null;
  const parseGroups = (segment: string): number[] | null => {
    if (segment.length === 0) return [];
    const parts = segment.split(":");
    const groups: number[] = [];
    for (const part of parts) {
      if (!/^[0-9a-fA-F]{1,4}$/.test(part)) return null;
      groups.push(Number.parseInt(part, 16));
    }
    return groups;
  };

  if (halves.length === 1) {
    const groups = parseGroups(normalized);
    return groups !== null && groups.length === 8 ? groups : null;
  }

  const head = parseGroups(halves[0] as string);
  const tail = parseGroups(halves[1] as string);
  if (head === null || tail === null || head.length + tail.length > 7) return null;
  const middle = new Array(8 - head.length - tail.length).fill(0);
  return [...head, ...middle, ...tail];
}

function isIpLiteral(hostname: string): boolean {
  const bare = hostname.startsWith("[") && hostname.endsWith("]") ? hostname.slice(1, -1) : hostname;
  return parseIpv4(bare) !== null || parseIpv6(bare) !== null;
}

/** Blocked IPv4 ranges: loopback, RFC1918 private, link-local (including the 169.254.169.254
 * cloud metadata address), carrier-grade NAT, multicast, reserved, documentation, and broadcast. */
export function isBlockedIpv4(octets: readonly number[]): boolean {
  const [a, b] = octets as [number, number, number, number];
  if (a === 127) return true; // loopback
  if (a === 10) return true; // RFC1918
  if (a === 172 && b >= 16 && b <= 31) return true; // RFC1918
  if (a === 192 && b === 168) return true; // RFC1918
  if (a === 169 && b === 254) return true; // link-local, incl. cloud metadata
  if (a === 100 && b >= 64 && b <= 127) return true; // carrier-grade NAT (RFC6598)
  if (a >= 224 && a <= 239) return true; // multicast
  if (a === 0) return true; // "this network"
  if (a === 192 && b === 0 && octets[2] === 0) return true; // IETF protocol assignments
  if (a === 192 && b === 0 && octets[2] === 2) return true; // documentation (TEST-NET-1)
  if (a === 198 && (b === 51 || b === 18 || b === 19)) return true; // documentation / benchmarking
  if (a === 203 && b === 0 && octets[2] === 113) return true; // documentation (TEST-NET-3)
  if (a >= 240) return true; // reserved (incl. 255.255.255.255 broadcast)
  return false;
}

/** Blocked IPv6 ranges: unspecified/loopback, unique-local, link-local, multicast, discard-only,
 * documentation, and (recursively) an IPv4-mapped address that itself resolves to a blocked range. */
export function isBlockedIpv6(groups: readonly number[]): boolean {
  if (groups.every((group) => group === 0)) return true; // ::
  if (groups.slice(0, 7).every((group) => group === 0) && groups[7] === 1) return true; // ::1
  if (((groups[0] as number) & 0xfe00) === 0xfc00) return true; // fc00::/7 unique local
  if (((groups[0] as number) & 0xffc0) === 0xfe80) return true; // fe80::/10 link-local
  if (((groups[0] as number) & 0xff00) === 0xff00) return true; // ff00::/8 multicast
  if (groups[0] === 0x100 && groups.slice(1, 7).every((group) => group === 0)) return true; // 100::/64 discard-only
  if (groups[0] === 0x2001 && groups[1] === 0xdb8) return true; // 2001:db8::/32 documentation
  // IPv4-mapped (::ffff:a.b.c.d): recurse into the embedded IPv4 address.
  if (groups.slice(0, 5).every((group) => group === 0) && groups[5] === 0xffff) {
    const mapped = [
      ((groups[6] as number) >> 8) & 0xff,
      (groups[6] as number) & 0xff,
      ((groups[7] as number) >> 8) & 0xff,
      (groups[7] as number) & 0xff,
    ];
    return isBlockedIpv4(mapped);
  }
  return false;
}

export function isBlockedIpAddress(address: string): boolean {
  const ipv4 = parseIpv4(address);
  if (ipv4 !== null) return isBlockedIpv4(ipv4);
  const ipv6 = parseIpv6(address);
  if (ipv6 !== null) return isBlockedIpv6(ipv6);
  // An address that is neither a valid IPv4 nor IPv6 literal cannot be trusted at all.
  return true;
}

/**
 * Format-only validation of a candidate URL: HTTPS scheme, no embedded
 * credentials, no explicit (non-default) port, and no IP-literal or
 * obviously-internal hostname. This never touches the network -- DNS
 * resolution and per-address blocking happen separately in
 * `preflightDns`, which must also be called for every redirect hop.
 */
export function validateCandidateUrl(rawUrl: string): URL {
  let url: URL;
  try {
    url = new URL(rawUrl);
  } catch {
    return blocked("invalid_url", "must be a well-formed URL");
  }
  if (url.protocol !== "https:") blocked("invalid_scheme", "only https URLs may be imported");
  if (url.username.length > 0 || url.password.length > 0) {
    blocked("credentials_in_url", "URLs with embedded credentials are not allowed");
  }
  if (url.port !== "") blocked("unexpected_port", "non-default ports are not allowed");

  const hostname = url.hostname.toLowerCase();
  if (isIpLiteral(hostname)) blocked("ip_literal_blocked", "IP-literal destinations are not allowed");
  if (isLocalHostname(hostname)) blocked("blocked_destination", "that hostname is not allowed");

  return url;
}

/** Resolves `hostname`'s A/AAAA records via a DNS-over-HTTPS provider using `fetchImpl` (so this
 * is exercised in tests the same way any other Worker outbound fetch is: an injectable HTTP call,
 * not a native DNS API workerd doesn't expose to user code). Returns the resolved IP address
 * strings, or an empty array if resolution fails/returns nothing. */
export async function resolveHostnameIpsViaDoh(
  hostname: string,
  fetchImpl: typeof fetch,
  signal?: AbortSignal,
): Promise<string[]> {
  const addresses: string[] = [];
  for (const type of ["A", "AAAA"] as const) {
    try {
      const response = await fetchImpl(
        `https://cloudflare-dns.com/dns-query?name=${encodeURIComponent(hostname)}&type=${type}`,
        { headers: { accept: "application/dns-json" }, signal: signal ?? null },
      );
      if (!response.ok) continue;
      const body = (await response.json()) as { Answer?: { type: number; data: string }[] };
      const wantType = type === "A" ? 1 : 28;
      for (const answer of body.Answer ?? []) {
        if (answer.type === wantType) addresses.push(answer.data);
      }
    } catch {
      // A failed lookup for one record type must not abort the other; an empty result overall
      // is handled by the caller as "could not resolve, treat as blocked".
    }
  }
  return addresses;
}

export interface DnsResolver {
  (hostname: string, signal?: AbortSignal): Promise<string[]>;
}

/** Resolves `hostname` and rejects unless every returned address is a public, non-internal IP.
 * Must be called for the initial URL and again for every redirect hop's new hostname. */
export async function preflightDns(
  hostname: string,
  resolveIps: DnsResolver,
  signal?: AbortSignal,
): Promise<void> {
  const addresses = await resolveIps(hostname, signal);
  if (addresses.length === 0) blocked("dns_resolution_failed", "could not resolve that host");
  for (const address of addresses) {
    if (isBlockedIpAddress(address)) blocked("blocked_destination", "that host resolves to a disallowed address");
  }
}
