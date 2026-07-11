from rob.discord.cogs import sends as sends_cog


class _FakeMember:
    def __init__(self, display_name: str) -> None:
        self.display_name = display_name


class _FakeGuild:
    def __init__(self, members: dict[int, _FakeMember]) -> None:
        self._members = members

    def get_member(self, user_id: int):
        return self._members.get(user_id)


def test_legacy_manual_send_methods_are_supported():
    assert sends_cog._MANUAL_METHODS == ["cashapp", "venmo", "paypal", "onlyfans", "loyalfans", "youpay", "throne", "other"]


def test_resolve_sub_attribution_blank_is_unclaimed():
    assert sends_cog._resolve_sub_attribution(_FakeGuild({}), None) == (None, None)
    assert sends_cog._resolve_sub_attribution(_FakeGuild({}), "   ") == (None, None)


def test_resolve_sub_attribution_plain_name_is_kept_as_send_name():
    assert sends_cog._resolve_sub_attribution(_FakeGuild({}), "  kitten  ") == ("kitten", None)


def test_resolve_sub_attribution_mention_links_user_with_display_name():
    guild = _FakeGuild({555: _FakeMember("Bobbin")})
    assert sends_cog._resolve_sub_attribution(guild, "<@555>") == ("Bobbin", 555)


def test_resolve_sub_attribution_legacy_nickname_mention_links_user():
    guild = _FakeGuild({777: _FakeMember("Samsung DVD Player")})
    assert sends_cog._resolve_sub_attribution(guild, "<@!777>") == ("Samsung DVD Player", 777)


def test_resolve_sub_attribution_mention_without_cached_member_still_links_user():
    assert sends_cog._resolve_sub_attribution(_FakeGuild({}), "<@555>") == (None, 555)
