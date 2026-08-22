from __future__ import annotations

import pytest

from bill.settings import Settings, SettingsError

BASE_ENV = {
    "BILL_DISCORD_TOKEN": "discord-secret",
    "BILL_WORKER_BASE_URL": "https://usebill.dev/",
    "BILL_WORKER_API_TOKEN": "worker-secret",
    "BILL_HOME_GUILD_ID": "123456789012345678",
}


def test_settings_loads_bill_environment() -> None:
    settings = Settings.from_env(
        {
            **BASE_ENV,
            "BILL_POLL_INTERVAL_SECONDS": "7",
            "BILL_TEST_GUILD_ID": "123456789012345678",
        }
    )

    assert settings.worker_base_url == "https://usebill.dev"
    assert settings.poll_interval_seconds == 7
    assert settings.test_guild_id == 123456789012345678
    assert settings.home_guild_id == 123456789012345678


def test_settings_requires_valid_home_guild() -> None:
    missing_home = {key: value for key, value in BASE_ENV.items() if key != "BILL_HOME_GUILD_ID"}
    with pytest.raises(SettingsError, match="BILL_HOME_GUILD_ID is required"):
        Settings.from_env(missing_home)

    with pytest.raises(SettingsError, match="Discord snowflake"):
        Settings.from_env({**BASE_ENV, "BILL_HOME_GUILD_ID": "not-a-snowflake"})


def test_settings_rejects_insecure_remote_worker() -> None:
    with pytest.raises(SettingsError, match="HTTPS"):
        Settings.from_env({**BASE_ENV, "BILL_WORKER_BASE_URL": "http://example.com"})


def test_settings_allows_local_http_worker() -> None:
    settings = Settings.from_env({**BASE_ENV, "BILL_WORKER_BASE_URL": "http://127.0.0.1:8787"})

    assert settings.worker_base_url == "http://127.0.0.1:8787"
