from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class BaseSettings:
    app_env: str
    log_level: str
    database_url: str
    rob_ops_host: str
    rob_ops_port: int
    rob_ops_secret: str | None
    rob_bot_notify_url: str | None
    throne_parse_test_sends_as_real_sends: bool
    throne_test_gifter_usernames: tuple[str, ...]
    throne_test_send_leaderboard_owner_user_id: int | None
    leaderboard_limit: int
    send_queue_loop_seconds: int
    public_leaderboard_cache_seconds: int
    rob_public_base_url: str
    inactivity_enabled_default: bool
    inactivity_loop_minutes: int
    inactivity_inactive_after_days: int
    inactivity_kick_grace_days: int
    inactivity_bootstrap_grace_days: int
    inactivity_final_notice_days: int
    inactivity_owner_user_id: int | None
    inactivity_notice_channel_id: int | None
    server_backup_enabled_default: bool
    server_backup_loop_minutes: int
    server_backup_required_approvals: int
    server_backup_major_change_threshold: int


@dataclass(frozen=True)
class WebhookSettings(BaseSettings):
    throne_webhook_host: str
    throne_webhook_port: int
    throne_webhook_base_url: str
    throne_webhook_require_signature: bool
    throne_public_key_pem: str | None
    throne_webhook_debug_log_payload: bool
    throne_webhook_timestamp_header: str
    throne_webhook_signature_header: str
    throne_webhook_signed_message_format: str
    throne_webhook_max_timestamp_skew_seconds: int


@dataclass(frozen=True)
class BotSettings(BaseSettings):
    discord_token: str
    bot_name: str
    # Voice-message speech-to-text. Uses a local faster-whisper model; disabled
    # until the operator installs faster-whisper and flips the flag on.
    voice_transcribe_enabled: bool
    voice_transcribe_model: str
    voice_transcribe_device: str
    voice_transcribe_compute_type: str
    voice_transcribe_language: str | None
    voice_transcribe_download_root: str | None
    voice_transcribe_beam_size: int
    voice_transcribe_max_duration_seconds: int
    voice_transcribe_max_file_mb: int
    voice_transcribe_max_concurrency: int


def _load_env_file(env_file: str | Path | None) -> None:
    disable_dotenv = os.getenv("PYTHON_DOTENV_DISABLED", "").strip().lower()
    if disable_dotenv in {"1", "true", "yes", "on"}:
        return

    if env_file is not None:
        load_dotenv(env_file)
        return
    load_dotenv()


def _env_str(name: str, default: str | None = None, *, required: bool = False) -> str:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        if required:
            raise RuntimeError(f"Missing required environment variable: {name}")
        return "" if default is None else default
    return value.strip()


def _env_int(name: str, default: int, *, minimum: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default

    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Environment variable {name} must be an integer.") from exc

    if minimum is not None and value < minimum:
        raise RuntimeError(f"Environment variable {name} must be at least {minimum}.")
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default

    lowered = raw.strip().lower()
    if lowered in {"1", "true", "yes", "y", "on"}:
        return True
    if lowered in {"0", "false", "no", "n", "off"}:
        return False
    raise RuntimeError(f"Environment variable {name} must be a boolean value.")


def _env_optional_int(name: str) -> int | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None

    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError(f"Environment variable {name} must be an integer.") from exc


def _env_lower_csv(name: str, default: str) -> tuple[str, ...]:
    raw = _env_str(name, default)
    return tuple(value.strip().lower() for value in raw.split(",") if value.strip())


def load_base_settings(env_file: str | Path | None = None) -> BaseSettings:
    _load_env_file(env_file)
    return BaseSettings(
        app_env=_env_str("APP_ENV", "dev"),
        log_level=_env_str("LOG_LEVEL", "INFO"),
        database_url=_env_str("DATABASE_URL", required=True),
        rob_ops_host=_env_str("ROB_OPS_HOST", "127.0.0.1"),
        rob_ops_port=_env_int("ROB_OPS_PORT", 8811, minimum=1),
        rob_ops_secret=_env_str("ROB_OPS_SECRET") or None,
        rob_bot_notify_url=_env_str("ROB_BOT_NOTIFY_URL") or None,
        throne_parse_test_sends_as_real_sends=_env_bool(
            "THRONE_PARSE_TEST_SENDS_AS_REAL_SENDS",
            False,
        ),
        throne_test_gifter_usernames=_env_lower_csv(
            "THRONE_TEST_GIFTER_USERNAMES",
            "marie_123",
        ),
        throne_test_send_leaderboard_owner_user_id=_env_optional_int(
            "THRONE_TEST_SEND_LEADERBOARD_OWNER_USER_ID"
        ),
        leaderboard_limit=_env_int("LEADERBOARD_LIMIT", 10, minimum=1),
        send_queue_loop_seconds=_env_int("SEND_QUEUE_LOOP_SECONDS", 10, minimum=1),
        public_leaderboard_cache_seconds=_env_int("PUBLIC_LEADERBOARD_CACHE_SECONDS", 60, minimum=1),
        rob_public_base_url=_env_str("ROB_PUBLIC_BASE_URL", "https://leaderboard.robthebot.com"),
        inactivity_enabled_default=_env_bool("INACTIVITY_ENABLED_DEFAULT", False),
        inactivity_loop_minutes=_env_int("INACTIVITY_LOOP_MINUTES", 10080, minimum=1),
        inactivity_inactive_after_days=_env_int("INACTIVITY_INACTIVE_AFTER_DAYS", 7, minimum=1),
        inactivity_kick_grace_days=_env_int("INACTIVITY_KICK_GRACE_DAYS", 14, minimum=1),
        inactivity_bootstrap_grace_days=_env_int("INACTIVITY_BOOTSTRAP_GRACE_DAYS", 21, minimum=1),
        inactivity_final_notice_days=_env_int("INACTIVITY_FINAL_NOTICE_DAYS", 7, minimum=1),
        inactivity_owner_user_id=_env_optional_int("INACTIVITY_OWNER_USER_ID"),
        inactivity_notice_channel_id=_env_optional_int("INACTIVITY_NOTICE_CHANNEL_ID"),
        server_backup_enabled_default=_env_bool("SERVER_BACKUP_ENABLED_DEFAULT", False),
        server_backup_loop_minutes=_env_int("SERVER_BACKUP_LOOP_MINUTES", 60, minimum=1),
        server_backup_required_approvals=_env_int("SERVER_BACKUP_REQUIRED_APPROVALS", 2, minimum=1),
        server_backup_major_change_threshold=_env_int(
            "SERVER_BACKUP_MAJOR_CHANGE_THRESHOLD", 5, minimum=1
        ),
    )


def load_webhook_settings(env_file: str | Path | None = None) -> WebhookSettings:
    base = load_base_settings(env_file)
    settings = WebhookSettings(
        app_env=base.app_env,
        log_level=base.log_level,
        database_url=base.database_url,
        rob_ops_host=base.rob_ops_host,
        rob_ops_port=base.rob_ops_port,
        rob_ops_secret=base.rob_ops_secret,
        rob_bot_notify_url=base.rob_bot_notify_url,
        throne_parse_test_sends_as_real_sends=base.throne_parse_test_sends_as_real_sends,
        throne_test_gifter_usernames=base.throne_test_gifter_usernames,
        throne_test_send_leaderboard_owner_user_id=base.throne_test_send_leaderboard_owner_user_id,
        leaderboard_limit=base.leaderboard_limit,
        send_queue_loop_seconds=base.send_queue_loop_seconds,
        public_leaderboard_cache_seconds=base.public_leaderboard_cache_seconds,
        inactivity_enabled_default=base.inactivity_enabled_default,
        inactivity_loop_minutes=base.inactivity_loop_minutes,
        inactivity_inactive_after_days=base.inactivity_inactive_after_days,
        inactivity_kick_grace_days=base.inactivity_kick_grace_days,
        inactivity_bootstrap_grace_days=base.inactivity_bootstrap_grace_days,
        inactivity_final_notice_days=base.inactivity_final_notice_days,
        inactivity_owner_user_id=base.inactivity_owner_user_id,
        inactivity_notice_channel_id=base.inactivity_notice_channel_id,
        server_backup_enabled_default=base.server_backup_enabled_default,
        server_backup_loop_minutes=base.server_backup_loop_minutes,
        server_backup_required_approvals=base.server_backup_required_approvals,
        server_backup_major_change_threshold=base.server_backup_major_change_threshold,
        throne_webhook_host=_env_str("THRONE_WEBHOOK_HOST", "127.0.0.1"),
        throne_webhook_port=_env_int("THRONE_WEBHOOK_PORT", 8080, minimum=1),
        throne_webhook_base_url=_env_str(
            "THRONE_WEBHOOK_BASE_URL",
            "https://throne.robthebot.com",
        ),
        throne_webhook_require_signature=_env_bool(
            "THRONE_WEBHOOK_REQUIRE_SIGNATURE",
            True,
        ),
        throne_public_key_pem=_env_str("THRONE_PUBLIC_KEY_PEM") or None,
        throne_webhook_debug_log_payload=_env_bool(
            "THRONE_WEBHOOK_DEBUG_LOG_PAYLOAD",
            False,
        ),
        throne_webhook_timestamp_header=_env_str(
            "THRONE_WEBHOOK_TIMESTAMP_HEADER",
            "X-Signature-Timestamp",
        ),
        throne_webhook_signature_header=_env_str(
            "THRONE_WEBHOOK_SIGNATURE_HEADER",
            "X-Signature-Ed25519",
        ),
        throne_webhook_signed_message_format=_env_str(
            "THRONE_WEBHOOK_SIGNED_MESSAGE_FORMAT",
            "timestamp_dot_body",
        ),
        throne_webhook_max_timestamp_skew_seconds=_env_int(
            "THRONE_WEBHOOK_MAX_TIMESTAMP_SKEW_SECONDS",
            300,
            minimum=0,
        ),
        rob_public_base_url=base.rob_public_base_url,
    )
    return settings


def load_bot_settings(env_file: str | Path | None = None) -> BotSettings:
    base = load_base_settings(env_file)
    return BotSettings(
        app_env=base.app_env,
        log_level=base.log_level,
        database_url=base.database_url,
        rob_ops_host=base.rob_ops_host,
        rob_ops_port=base.rob_ops_port,
        rob_ops_secret=base.rob_ops_secret,
        rob_bot_notify_url=base.rob_bot_notify_url,
        throne_parse_test_sends_as_real_sends=base.throne_parse_test_sends_as_real_sends,
        throne_test_gifter_usernames=base.throne_test_gifter_usernames,
        throne_test_send_leaderboard_owner_user_id=base.throne_test_send_leaderboard_owner_user_id,
        leaderboard_limit=base.leaderboard_limit,
        send_queue_loop_seconds=base.send_queue_loop_seconds,
        public_leaderboard_cache_seconds=base.public_leaderboard_cache_seconds,
        rob_public_base_url=base.rob_public_base_url,
        inactivity_enabled_default=base.inactivity_enabled_default,
        inactivity_loop_minutes=base.inactivity_loop_minutes,
        inactivity_inactive_after_days=base.inactivity_inactive_after_days,
        inactivity_kick_grace_days=base.inactivity_kick_grace_days,
        inactivity_bootstrap_grace_days=base.inactivity_bootstrap_grace_days,
        inactivity_final_notice_days=base.inactivity_final_notice_days,
        inactivity_owner_user_id=base.inactivity_owner_user_id,
        inactivity_notice_channel_id=base.inactivity_notice_channel_id,
        server_backup_enabled_default=base.server_backup_enabled_default,
        server_backup_loop_minutes=base.server_backup_loop_minutes,
        server_backup_required_approvals=base.server_backup_required_approvals,
        server_backup_major_change_threshold=base.server_backup_major_change_threshold,
        discord_token=_env_str("DISCORD_TOKEN", required=True),
        bot_name=_env_str("BOT_NAME", "Rob"),
        voice_transcribe_enabled=_env_bool("VOICE_TRANSCRIBE_ENABLED", False),
        voice_transcribe_model=_env_str("VOICE_TRANSCRIBE_MODEL", "base"),
        voice_transcribe_device=_env_str("VOICE_TRANSCRIBE_DEVICE", "cpu"),
        voice_transcribe_compute_type=_env_str("VOICE_TRANSCRIBE_COMPUTE_TYPE", "int8"),
        voice_transcribe_language=_env_str("VOICE_TRANSCRIBE_LANGUAGE") or None,
        voice_transcribe_download_root=_env_str("VOICE_TRANSCRIBE_DOWNLOAD_ROOT") or None,
        voice_transcribe_beam_size=_env_int("VOICE_TRANSCRIBE_BEAM_SIZE", 1, minimum=1),
        voice_transcribe_max_duration_seconds=_env_int(
            "VOICE_TRANSCRIBE_MAX_DURATION_SECONDS", 300, minimum=1
        ),
        voice_transcribe_max_file_mb=_env_int("VOICE_TRANSCRIBE_MAX_FILE_MB", 25, minimum=1),
        voice_transcribe_max_concurrency=_env_int(
            "VOICE_TRANSCRIBE_MAX_CONCURRENCY", 1, minimum=1
        ),
    )


def configure_logging(log_level: str) -> None:
    logging.basicConfig(
        level=log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
