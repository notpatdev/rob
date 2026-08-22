from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlparse


class SettingsError(ValueError):
    """Raised when Bill's environment is incomplete or unsafe."""


def _required(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise SettingsError(f"{name} is required")
    return value


def _positive_int(
    environ: Mapping[str, str],
    name: str,
    default: int,
    *,
    maximum: int,
) -> int:
    raw = environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise SettingsError(f"{name} must be an integer") from exc
    if value < 1 or value > maximum:
        raise SettingsError(f"{name} must be between 1 and {maximum}")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    discord_token: str
    worker_base_url: str
    worker_api_token: str
    poll_interval_seconds: int = 5
    notification_batch_size: int = 10
    notification_lease_seconds: int = 60
    test_guild_id: int | None = None
    log_level: str = "INFO"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        values = os.environ if environ is None else environ
        base_url = _required(values, "BILL_WORKER_BASE_URL").rstrip("/")
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise SettingsError("BILL_WORKER_BASE_URL must be an absolute HTTP(S) URL")
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise SettingsError("BILL_WORKER_BASE_URL must use HTTPS outside local development")

        raw_test_guild = values.get("BILL_TEST_GUILD_ID", "").strip()
        test_guild_id: int | None = None
        if raw_test_guild:
            if not raw_test_guild.isdecimal():
                raise SettingsError("BILL_TEST_GUILD_ID must be a Discord snowflake")
            test_guild_id = int(raw_test_guild)

        log_level = values.get("BILL_LOG_LEVEL", "INFO").strip().upper()
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise SettingsError("BILL_LOG_LEVEL is invalid")

        return cls(
            discord_token=_required(values, "BILL_DISCORD_TOKEN"),
            worker_base_url=base_url,
            worker_api_token=_required(values, "BILL_WORKER_API_TOKEN"),
            poll_interval_seconds=_positive_int(
                values,
                "BILL_POLL_INTERVAL_SECONDS",
                5,
                maximum=300,
            ),
            notification_batch_size=_positive_int(
                values,
                "BILL_NOTIFICATION_BATCH_SIZE",
                10,
                maximum=50,
            ),
            notification_lease_seconds=_positive_int(
                values,
                "BILL_NOTIFICATION_LEASE_SECONDS",
                60,
                maximum=600,
            ),
            test_guild_id=test_guild_id,
            log_level=log_level,
        )
