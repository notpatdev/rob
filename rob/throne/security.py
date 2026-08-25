from __future__ import annotations

import hashlib
import hmac
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key


def hash_webhook_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def secret_matches(
    *,
    provided_secret: str,
    stored_secret: str | None,
    stored_secret_hash: str | None,
) -> bool:
    provided_hash = hash_webhook_secret(provided_secret)
    if stored_secret_hash and hmac.compare_digest(provided_hash, stored_secret_hash):
        return True
    if stored_secret and hmac.compare_digest(provided_secret, stored_secret):
        return True
    return False


def validate_timestamp_header(
    value: str | None,
    *,
    max_skew_seconds: int,
) -> bool:
    if value is None:
        return False
    stripped = value.strip()
    if not stripped.isdigit():
        return False
    skew = abs(int(time.time()) - int(stripped))
    return skew <= max_skew_seconds


def build_signed_message(
    *,
    timestamp: str,
    raw_body: bytes,
    signed_message_format: str,
) -> bytes:
    if signed_message_format == "timestamp_dot_body":
        return f"{timestamp}.".encode("utf-8") + raw_body
    if signed_message_format == "timestamp_concat_body":
        return timestamp.encode("utf-8") + raw_body
    return raw_body


def verify_ed25519_signature(
    *,
    public_key_pem: str,
    signature_hex: str,
    message: bytes,
) -> bool:
    try:
        signature_bytes = bytes.fromhex(signature_hex)
        if len(signature_bytes) != 64:
            return False
        public_key = load_pem_public_key(public_key_pem.encode("utf-8"))
        if not isinstance(public_key, Ed25519PublicKey):
            return False
        public_key.verify(signature_bytes, message)
        return True
    except Exception:
        return False
