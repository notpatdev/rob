from __future__ import annotations

import hashlib
import re

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rob.models import SendRecord

_PUBLIC_ID_RE = re.compile(r"^ROB-(\d+)-([A-F0-9]{8})$")


def generate_public_send_id(
    send_id: int,
    *,
    guild_id: int,
    stable_seed: str | None = None,
) -> str:
    digest_source = f"{send_id}:{guild_id}:{stable_seed or ''}"
    digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:8].upper()
    return f"ROB-{send_id:06d}-{digest}"


def build_public_send_id(send: SendRecord) -> str:
    stable_seed = (
        send.event_id
        or send.fallback_event_hash
        or f"{send.created_at.isoformat()}:{send.domme_user_id}:{send.amount_cents}:{send.sent_at.isoformat()}"
    )
    return generate_public_send_id(
        send.id,
        guild_id=send.guild_id,
        stable_seed=stable_seed,
    )


def parse_public_send_id(value: str) -> tuple[int, str] | None:
    match = _PUBLIC_ID_RE.fullmatch(value.strip().upper())
    if match is None:
        return None
    return int(match.group(1)), match.group(2)
