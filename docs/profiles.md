# Profiles

`/profile [member]` is a global application command that only runs in guild
context. In Bill's configured home guild it resolves the member's global
profile. In every other guild it resolves a published server profile:

- **linked** profiles inherit the latest global profile and store only explicit
  identity overrides, local links, and inherited-link visibility choices;
- **independent** profiles own a complete document for that guild.

A linked profile never pins or copies a global document. Empty override values
are represented separately from inheritance, so deliberately clearing a bio is
different from inheriting it. The linked wizard can also hide individual
inherited links while retaining the rest.

## Private setup

Missing self profiles start with a normal ephemeral guild prompt. After the
member confirms, Bill sends one normal DM introduction and then a direct
Components V2 wizard. Missing profiles for an explicitly selected other member
never start setup.

Drafts are private D1 records. Each mutation carries the last observed revision;
stale, foreign-user, wrong-guild, and completed controls fail safely.
Fixed identity selections are persisted without completing the identity step,
so a restart can reconstruct partial progress. Restart is explicit and
revision-checked. Publication changes the public root only during the final
review action.

The four orientations are Dom/me, Submissive, Switch leaning Dom/me, and Switch
leaning Submissive. All support pronouns, DM status, an optional 300-character
bio, and social links. Dom/me and switch profiles also support payment links and
Throne. Submissive and switch profiles support up to three aliases and the
public send-stat preference. Switch profiles support both honourifics and
submissive labels.

## Links and privacy

Profiles resolve at most twelve enabled links. Labels are at most 40 characters
and URLs at most 500 characters. Public links are HTTPS. Imported Linktree,
AllMyLinks, Beacons, and generic pages are static HTML only; JavaScript is never
executed. The Worker validates DNS and every redirect, blocks private/reserved
destinations, enforces a five-second deadline and 512 KiB body limit, and stores
only normalized candidates rather than raw HTML.

Public profile cards do not include webhook URLs, route secrets, creator IDs,
or other credentials. Payment and social buttons ask the Worker for the current
guild-resolved profile and open ephemeral link details for the viewer. A Throne
webhook URL appears only to its owner when first issued or explicitly rotated.

## Send statistics

Aliases affect future sends only. Private and anonymous sends are never
attributed. A sender is attributed only when their normalized sender
username/display name maps to exactly one effective profile owner in the
recipient guild. Opted-in stats are per guild and grouped by currency; Bill does
not convert currencies.
