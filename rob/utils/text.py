from __future__ import annotations

import hashlib
import re

_USER_MENTION_RE = re.compile(r"^<@!?(\d+)>$")


def collapse_whitespace(value: str) -> str:
    return " ".join(value.split())


def parse_user_mention(value: str | None) -> int | None:
    """Return the Discord user id if value is exactly a user mention.

    Discord inserts <@123> (or legacy <@!123>) when someone picks a member
    from the @-autocomplete in a free-text slash-command field; attribute the
    send to that user rather than storing the raw token as a name.
    """

    if not value:
        return None
    match = _USER_MENTION_RE.match(value.strip())
    if match is None:
        return None
    return int(match.group(1))


def normalize_sender_name(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = collapse_whitespace(value.strip())
    return cleaned or None


def short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
