from rob.config.guilds import (
    MAIN_GUILD_ID,
    TEST_GUILD_ID,
    is_main_guild,
    is_new_system_guild,
    is_test_guild,
)


def test_test_guild_helper_matches_constant():
    assert is_test_guild(TEST_GUILD_ID) is True
    assert is_test_guild(MAIN_GUILD_ID) is False


def test_main_guild_helper_matches_constant():
    assert is_main_guild(MAIN_GUILD_ID) is True
    assert is_main_guild(TEST_GUILD_ID) is False


def test_guild_helpers_handle_none_and_other_ids():
    assert is_test_guild(None) is False
    assert is_main_guild(None) is False
    assert is_test_guild(0) is False
    assert is_main_guild(123456789) is False


def test_new_system_guild_helper_covers_main_and_test():
    assert is_new_system_guild(MAIN_GUILD_ID) is True
    assert is_new_system_guild(TEST_GUILD_ID) is True


def test_new_system_guild_helper_rejects_other_ids_and_none():
    assert is_new_system_guild(None) is False
    assert is_new_system_guild(987654321) is False


def test_main_and_test_guild_ids_are_distinct():
    assert MAIN_GUILD_ID != TEST_GUILD_ID
