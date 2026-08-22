"""Compact reversible tokens for Discord's 100-character component ID limit.

Persistent controls must carry resource, user, guild, and revision context so a
restart can route and re-authorize them. UUID integers and decimal snowflakes
are encoded in base 36 rather than dropping any binding context to save space.
"""

from __future__ import annotations

import uuid

_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"


def encode_uint(value: int | str) -> str:
    number = int(value)
    if number < 0:
        raise ValueError("component integers cannot be negative")
    if number == 0:
        return "0"
    encoded = ""
    while number:
        number, remainder = divmod(number, 36)
        encoded = _ALPHABET[remainder] + encoded
    return encoded


def decode_uint(value: str) -> int:
    return int(value, 36)


def encode_resource_id(value: str) -> str:
    """Compress UUIDs while retaining a raw-token fallback for test/legacy IDs."""
    try:
        return f"u{encode_uint(uuid.UUID(value).int)}"
    except ValueError:
        return f"r{value}"


def decode_resource_id(value: str) -> str:
    if value.startswith("u"):
        return str(uuid.UUID(int=decode_uint(value[1:])))
    if value.startswith("r"):
        return value[1:]
    raise ValueError("unknown component resource encoding")
