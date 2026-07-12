from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

try:
    from bot import channels as _channels
except Exception as exc:  # noqa: BLE001 - config should degrade cleanly
    _channels = None
    _CHANNELS_IMPORT_ERROR: Exception | None = exc
else:
    _CHANNELS_IMPORT_ERROR = None

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BotConfig:
    bot_name: str
    event_name: str
    discord_token: str
    guild_id: int
    registration_channel_id: int
    event_report_channel_id: int
    domme_role_id: int
    submissive_role_id: int
    moderation_role_id: int
    event_ban_role_id: int
    leaderboard_channel_id: int
    send_track_channel_id: int
    events_config_path: Path
    database_path: Path
    throne_poll_interval_seconds: int
    throne_poll_per_domme_delay_seconds: float
    throne_http_timeout_seconds: float
    throne_user_agent: str
    # Throne webhook server settings
    throne_webhook_port: int
    throne_webhook_base_url: str | None
    throne_webhook_require_signature: bool
    throne_webhook_debug_log_payload: bool
    throne_public_key_pem: str | None
    throne_webhook_timestamp_header: str
    throne_webhook_signature_header: str
    throne_webhook_signed_message_format: str
    throne_webhook_max_timestamp_skew_seconds: int
    # Carl-bot warn DM feature (optional)
    warn_log_channel_id: int
    carlbot_user_id: int


_DEFAULT_THRONE_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 RobBot/1.0"
)


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, value)


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(minimum, value)


def _load_channel_id(name: str) -> int:
    if _channels is None:
        return 0
    raw = getattr(_channels, name, 0)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        log.warning(
            "bot/channels.py has an invalid %s value %r. Rob will treat it as 0.",
            name,
            raw,
        )
        return 0
    if value < 0:
        log.warning(
            "bot/channels.py has a negative %s value %r. Rob will treat it as 0.",
            name,
            raw,
        )
        return 0
    if value == 0 and name not in {
        "EVENT_BAN_ROLE_ID",
        "EVENT_REPORT_CHANNEL_ID",
        "WARN_LOG_CHANNEL_ID",
        "CARLBOT_USER_ID",
    }:
        log.warning(
            "bot/channels.py is missing %s. Some Discord features will stay offline.",
            name,
        )
    return value


def load_config() -> BotConfig:
    load_dotenv()

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("Missing required environment variable: DISCORD_TOKEN")

    if _CHANNELS_IMPORT_ERROR is not None:
        log.warning(
            "Could not import bot/channels.py (%s). Copy bot/channels.example.py to bot/channels.py "
            "and fill in your Discord IDs. Rob will keep running with 0-valued IDs for now.",
            _CHANNELS_IMPORT_ERROR,
        )

    events_config_path = Path(
        os.getenv(
            "EVENTS_CONFIG_PATH",
            Path(__file__).resolve().parent.parent / "config" / "events.json",
        )
    )

    return BotConfig(
        bot_name=os.getenv("BOT_NAME", "Rob"),
        event_name=os.getenv("EVENT_NAME", "Mother's Day Event"),
        discord_token=token,
        guild_id=_load_channel_id("GUILD_ID"),
        registration_channel_id=_load_channel_id("REGISTRATION_CHANNEL_ID"),
        event_report_channel_id=_load_channel_id("EVENT_REPORT_CHANNEL_ID"),
        domme_role_id=_load_channel_id("DOMME_ROLE_ID"),
        submissive_role_id=_load_channel_id("SUBMISSIVE_ROLE_ID"),
        moderation_role_id=_load_channel_id("MODERATION_ROLE_ID"),
        event_ban_role_id=_load_channel_id("EVENT_BAN_ROLE_ID"),
        leaderboard_channel_id=_load_channel_id("LEADERBOARD_CHANNEL_ID"),
        send_track_channel_id=_load_channel_id("SEND_TRACK_CHANNEL_ID"),
        events_config_path=events_config_path,
        database_path=Path(
            os.getenv("DATABASE_PATH", "/opt/rob-the-bot/data/rob_the_bot.sqlite3")
        ),
        throne_poll_interval_seconds=_env_int(
            "THRONE_POLL_INTERVAL_SECONDS", 30, minimum=30
        ),
        throne_poll_per_domme_delay_seconds=_env_float(
            "THRONE_POLL_PER_DOMME_DELAY_SECONDS", 3.0, minimum=0.0
        ),
        throne_http_timeout_seconds=_env_float(
            "THRONE_HTTP_TIMEOUT_SECONDS", 10.0, minimum=1.0
        ),
        throne_user_agent=os.getenv("THRONE_USER_AGENT") or _DEFAULT_THRONE_USER_AGENT,
        throne_webhook_port=_env_int("THRONE_WEBHOOK_PORT", 8080, minimum=1),
        throne_webhook_base_url=os.getenv("THRONE_WEBHOOK_BASE_URL") or None,
        throne_webhook_require_signature=(
            os.getenv("THRONE_WEBHOOK_REQUIRE_SIGNATURE", "true").strip().lower()
            != "false"
        ),
        throne_webhook_debug_log_payload=(
            os.getenv("THRONE_WEBHOOK_DEBUG_LOG_PAYLOAD", "false").strip().lower()
            == "true"
        ),
        throne_public_key_pem=os.getenv("THRONE_PUBLIC_KEY_PEM") or None,
        throne_webhook_timestamp_header=os.getenv(
            "THRONE_WEBHOOK_TIMESTAMP_HEADER", "X-Signature-Timestamp"
        ),
        throne_webhook_signature_header=os.getenv(
            "THRONE_WEBHOOK_SIGNATURE_HEADER", "X-Signature-Ed25519"
        ),
        throne_webhook_signed_message_format=os.getenv(
            "THRONE_WEBHOOK_SIGNED_MESSAGE_FORMAT", "timestamp_dot_body"
        ),
        throne_webhook_max_timestamp_skew_seconds=_env_int(
            "THRONE_WEBHOOK_MAX_TIMESTAMP_SKEW_SECONDS", 300, minimum=0
        ),
        warn_log_channel_id=_load_channel_id("WARN_LOG_CHANNEL_ID"),
        carlbot_user_id=_load_channel_id("CARLBOT_USER_ID"),
    )
