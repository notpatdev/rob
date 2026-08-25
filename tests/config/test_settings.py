from __future__ import annotations

import rob.config.settings as settings_module
from rob.config.settings import load_base_settings, load_bot_settings, load_webhook_settings
from rob.services.registration_service import sanitize_webhook_base_url


def test_load_base_settings_only_requires_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://example/db")
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)

    settings = load_base_settings()

    assert settings.database_url == "postgresql://example/db"
    assert settings.app_env == "dev"
    assert settings.rob_ops_host == "127.0.0.1"
    assert settings.rob_ops_port == 8811
    assert settings.rob_bot_notify_url is None
    assert settings.rob_public_base_url == "https://leaderboard.robthebot.com"


def test_load_bot_settings_requires_discord_token(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://example/db")
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)

    try:
        load_bot_settings()
    except RuntimeError as exc:
        assert "DISCORD_TOKEN" in str(exc)
    else:
        raise AssertionError("Expected DISCORD_TOKEN requirement to fail.")


def test_load_webhook_settings_does_not_require_discord_token(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://example/db")
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)

    settings = load_webhook_settings()

    assert settings.database_url == "postgresql://example/db"
    assert settings.throne_webhook_require_signature is True
    assert settings.throne_test_gifter_usernames == ("marie_123",)
    assert settings.leaderboard_limit == 10


def test_sanitize_webhook_base_url_handles_bad_equals_quotes_and_slash():
    raw = "  '=https://throne.robthebot.com/'  "
    assert sanitize_webhook_base_url(raw) == "https://throne.robthebot.com"


def test_load_webhook_url_sanitizes_when_generating_registration_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://example/db")
    monkeypatch.setenv("THRONE_WEBHOOK_BASE_URL", "==https://throne.robthebot.com/")
    settings = load_webhook_settings()
    assert sanitize_webhook_base_url(settings.throne_webhook_base_url) == "https://throne.robthebot.com"


def test_load_base_settings_supports_test_sender_and_leaderboard_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://example/db")
    monkeypatch.setenv("ROB_OPS_HOST", "127.0.0.2")
    monkeypatch.setenv("ROB_OPS_PORT", "9911")
    monkeypatch.setenv("ROB_OPS_SECRET", "shared-secret")
    monkeypatch.setenv("ROB_BOT_NOTIFY_URL", "https://bot-01.robthebot.com/ops/sends/process")
    monkeypatch.setenv("THRONE_TEST_GIFTER_USERNAMES", "marie_123, test_sender ")
    monkeypatch.setenv("THRONE_TEST_SEND_LEADERBOARD_OWNER_USER_ID", "42")
    monkeypatch.setenv("LEADERBOARD_LIMIT", "15")
    monkeypatch.setenv("ROB_PUBLIC_BASE_URL", "https://example.com")

    settings = load_base_settings()

    assert settings.rob_ops_host == "127.0.0.2"
    assert settings.rob_ops_port == 9911
    assert settings.rob_ops_secret == "shared-secret"
    assert settings.rob_bot_notify_url == "https://bot-01.robthebot.com/ops/sends/process"
    assert settings.throne_test_gifter_usernames == ("marie_123", "test_sender")
    assert settings.throne_test_send_leaderboard_owner_user_id == 42
    assert settings.leaderboard_limit == 15
    assert settings.rob_public_base_url == "https://example.com"


def test_load_base_settings_skips_dotenv_when_disabled(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://example/db")
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")

    def _raise_if_called(*_args, **_kwargs):
        raise RuntimeError("load_dotenv should not be called when disabled")

    monkeypatch.setattr(settings_module, "load_dotenv", _raise_if_called)

    settings = load_base_settings()
    assert settings.database_url == "postgresql://example/db"
