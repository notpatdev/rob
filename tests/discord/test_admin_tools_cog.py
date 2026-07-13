from __future__ import annotations

from rob.discord.cogs.admin_tools import _parse_user_id


def test_parse_user_id_accepts_raw_and_mention():
    assert _parse_user_id("123456789012345678") == 123456789012345678
    assert _parse_user_id("<@123456789012345678>") == 123456789012345678
    assert _parse_user_id("<@!123456789012345678>") == 123456789012345678


def test_parse_user_id_rejects_non_numeric():
    assert _parse_user_id("abc123") is None
