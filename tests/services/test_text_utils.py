from rob.utils.text import parse_user_mention


def test_parse_user_mention_standard():
    assert parse_user_mention("<@123>") == 123


def test_parse_user_mention_legacy_nickname_form():
    assert parse_user_mention("<@!456>") == 456


def test_parse_user_mention_tolerates_surrounding_whitespace():
    assert parse_user_mention("  <@789> ") == 789


def test_parse_user_mention_rejects_non_mentions():
    assert parse_user_mention(None) is None
    assert parse_user_mention("") is None
    assert parse_user_mention("kitten") is None
    # A mention with trailing text is not a pure user reference.
    assert parse_user_mention("<@123> and friends") is None
    # Role and channel mentions are not user mentions.
    assert parse_user_mention("<@&123>") is None
    assert parse_user_mention("<#123>") is None
