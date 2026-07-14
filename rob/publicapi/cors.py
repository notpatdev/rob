"""CORS origin resolution for the public API.

``PUBLIC_API_ALLOWED_ORIGIN`` is a comma-separated allow-list. Entries may be:

* an exact origin (``https://robthebot.com``),
* a wildcard pattern (``https://*.lovableproject.com``), matched with fnmatch,
* or ``*`` to allow any origin.

When the request's ``Origin`` matches, it is *reflected* back (browsers require
``Access-Control-Allow-Origin`` to equal the request origin, or ``*``). This lets
one deployment serve the production site and an ephemeral preview host at once.
"""

from __future__ import annotations

import fnmatch


def parse_allowed_origins(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def resolve_cors_origin(request_origin: str | None, allowed: list[str]) -> str | None:
    """Return the value for ``Access-Control-Allow-Origin``, or None to omit it.

    * ``*`` in the allow-list reflects the request origin (or ``*`` if none).
    * Otherwise an exact or wildcard match reflects the request origin.
    * No Origin header / no match falls back to the first configured origin, so
      the canonical site is always allowed and unlisted origins are refused.
    """
    if not allowed:
        return None
    if "*" in allowed:
        return request_origin or "*"
    if request_origin:
        candidate = request_origin.lower()
        for pattern in allowed:
            lowered = pattern.lower()
            if lowered == candidate:
                return request_origin
            if "*" in lowered and fnmatch.fnmatch(candidate, lowered):
                return request_origin
    return allowed[0]
