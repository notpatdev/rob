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
member confirms, Bill sends a friendly normal DM introduction with a durable,
user-bound **Start** button. Start replaces the introduction with the Components
V2 wizard; because the control reloads the saved draft from D1, it continues to
work after a bot restart. Missing profiles for an explicitly selected other
member never start setup.

Drafts are private D1 records. Each mutation carries the last observed revision;
stale, foreign-user, wrong-guild, and completed controls fail safely.
The wizard edits one Components V2 message in place and durably stores its exact
stage and substep before rerendering. The guided order is orientation, pronouns,
conditional titles/labels, DM status, bio, profile colour, links, conditional
Throne and/or aliases/stats, then review. Conditional stages change the displayed
`Step X of Y` total. Back, Continue, resume, and review edits therefore reconstruct
the same screen after a restart instead of inferring progress from Discord.
Fixed selections are persisted without prematurely completing their logical
profile section.
Global and independent profiles must deliberately choose Open, By Request,
After Tribute, or Closed before saving identity. A linked server profile may
instead choose **Use global setting**, which removes its server-specific DM
status override. That deliberate choice is tracked separately from the saved
status value, so active drafts created before the menu cannot mistake an old
implicit Open value for user intent. The review screen exposes the same
revision-bound menu for changing a status or restoring linked inheritance.
Restart is explicit and revision-checked. Publication changes the public root
only during the final review action.

The four orientations are Dom/me, Submissive, Switch leaning Dom/me, and Switch
leaning Submissive. All support pronouns, DM status, an optional 300-character
bio, and social links. Dom/me and switch profiles also support payment links and
Throne. Submissive and switch profiles support up to three aliases and the
public send-stat preference. Switch profiles support both honourifics and
submissive labels.

Published profile cards may use Bill's documented Blue, Purple, Rose, Red,
Orange, Gold, Emerald, or Teal presets, a strict six-digit custom RGB hex value,
or no colour. Setup containers remain neutral. A linked server profile inherits
the global colour unless the member explicitly selects a local colour or
deliberately clears it to **No colour**.

Throne connection is a verified flow rather than a success-shaped attachment.
Bill resolves the submitted profile, asks the member to confirm the handle, and
then issues or reuses the private webhook. The owner sees a numbered set of
generic Throne navigation instructions and the URL once. **Check Connection**
queries the Worker for `webhook_verified_at`; an unverified test remains on the
same screen with troubleshooting, while a verified test unlocks Continue. An
already-owned verified creator can continue without rotating its secret, and
Skip remains available.

## Links and privacy

Profiles resolve at most twelve enabled links. Labels are at most 40 characters
and URLs at most 500 characters. Public links are HTTPS. Imported Linktree,
AllMyLinks, Beacons, and generic pages are static HTML only; JavaScript is never
executed. The Worker validates DNS and every redirect, blocks private/reserved
destinations, enforces a five-second deadline and 512 KiB body limit, and stores
only normalized candidates rather than raw HTML.

Public profile cards use the member's current Discord display name and avatar,
compact orientation and DM metadata, and only non-empty profile details. They do
not include webhook URLs, route secrets, creator IDs, or other credentials.
Payment and social buttons ask the Worker for the current guild-resolved profile
and open ephemeral link details with up to five direct HTTPS buttons per row. A
Throne webhook URL appears only to its owner when first issued or explicitly
rotated.

## Send statistics

Aliases affect future sends only. Private and anonymous sends are never
attributed. A sender is attributed only when their normalized sender
username/display name maps to exactly one effective profile owner in the
recipient guild. Opted-in stats are per guild and grouped by currency; Bill does
not convert currencies.
