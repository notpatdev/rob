# Roadmap

These are design directions, not implemented commands or promises. New
operator-facing behavior should remain disabled until its authorization,
privacy, audit, and rollback model is reviewed.

## Secure development-only commands

Future development commands should be registered only in explicitly configured
non-production guilds, require an allowlisted operator plus Discord permission,
and fail closed when environment identity is ambiguous. They must never reveal
webhook secrets, bearer tokens, raw Throne payloads, private drafts, or arbitrary
D1 rows. Production builds should omit registration rather than hide commands
only in UI.

## Support tickets

A future support flow may create a minimal ticket containing a random reference,
category, affected guild/user identifiers, timestamps, and user-supplied
description. Private profile values and secrets should be opt-in redacted
attachments, never automatic copies. Tickets need retention limits, explicit
staff roles, access logging, and a deletion path before launch.

## Diagnostics

Diagnostics should report bounded health facts: deployment version, migration
level, queue counts, lease age ranges, profile/setup state labels, and
permission checks. They should use typed allowlisted queries and coarse counts
rather than arbitrary SQL or document dumps. Any guild/user-specific diagnostic
must repeat the same authorization checks as the underlying operation.

## Audit trail

Profile publications already have immutable history. A broader audit design
could record actor, action, resource type, resource identifier, old/new version,
request correlation ID, and timestamp. It must exclude secrets and large
payloads, define retention, and distinguish user action from automated
projection repair. Audit writes should share the state-changing D1 batch where
the action requires atomic evidence.

## Feature flags

Flags should be typed, default off, scoped explicitly (global, guild, or user),
and evaluated in the Worker so bot restarts cannot change authority. Changes
need an actor, reason, expiry, and audit record. Security-sensitive code paths
must not use a client-only flag, and removing a flag should include deleting its
dead branch and tests rather than leaving permanent conditional complexity.
