from __future__ import annotations

from rob.database.repositories.bot_settings import _to_text


def test_to_text_unwraps_asyncpg_jsonb_string_value():
    assert _to_text('{"value": "true"}') == "true"


def test_to_text_unwraps_jsonb_dict_value():
    assert _to_text({"value": "false"}) == "false"


def test_to_text_preserves_plain_text_values():
    assert _to_text("on") == "on"
