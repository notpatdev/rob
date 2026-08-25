from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

from rob.config.guilds import MAIN_GUILD_ID, TEST_GUILD_ID
from rob.models import LatestTrackedSend, LeaderboardEntry, PersonalStatsSummary
from rob.discord.cogs.leaderboards import LeaderboardsCog

# Neither main nor test: leaderboard view gating must not apply here.
OTHER_GUILD_ID = 424242424242424242


class _FakeResponse:
    def __init__(self):
        self.messages: list[dict] = []

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)


class _FakeMember:
    def __init__(self, *, user_id: int, display_name: str, role_ids: list[int]):
        self.id = user_id
        self.display_name = display_name
        self.mention = f"<@{user_id}>"
        self.roles = [SimpleNamespace(id=role_id) for role_id in role_ids]


class _FakeGuild:
    def __init__(self, members: dict[int, _FakeMember], guild_id: int = 1):
        self.id = guild_id
        self._members = members

    def get_member(self, user_id: int):
        return self._members.get(user_id)


class _FakeInteraction:
    def __init__(self, *, user: _FakeMember, guild: _FakeGuild):
        self.guild = guild
        self.user = user
        self.response = _FakeResponse()


class _FakeLeaderboardsRepo:
    async def get_domme_stats(self, *_args, **_kwargs):
        return PersonalStatsSummary(total_cents=12345, send_count=7)

    async def get_domme_rank(self, *_args, **_kwargs):
        return 1

    async def get_domme_latest_send(self, *_args, **_kwargs):
        now = datetime.now(timezone.utc)
        return LatestTrackedSend(
            id=1,
            domme_user_id=10,
            sub_user_id=20,
            sub_name="gifter",
            amount_cents=1099,
            currency="USD",
            method="paypal",
            source="manual:paypal",
            item_name="Flowers",
            item_image_url="https://example.com/item.png",
            sent_at=now,
        )

    async def get_domme_top_sending_sub(self, *_args, **_kwargs):
        return LeaderboardEntry(label="<@20>", user_id=20, total_cents=5000, send_count=2)

    async def get_sub_stats(self, *_args, **_kwargs):
        return PersonalStatsSummary(total_cents=54321, send_count=4)

    async def get_sub_latest_send(self, *_args, **_kwargs):
        now = datetime.now(timezone.utc)
        return LatestTrackedSend(
            id=2,
            domme_user_id=30,
            sub_user_id=10,
            sub_name="Pat",
            amount_cents=2099,
            currency="USD",
            method="paypal",
            source="manual:paypal",
            item_name="AirPods",
            item_image_url="https://example.com/item2.png",
            sent_at=now,
        )

    async def get_sub_top_domme(self, *_args, **_kwargs):
        return LeaderboardEntry(label="<@30>", user_id=30, total_cents=3000, send_count=2)


class _FakeBot:
    def __init__(
        self,
        *,
        domme_role_id: int | None,
        sub_role_id: int | None,
        leaderboard_view_role_id: int | None = None,
        mod_role_id: int | None = None,
    ):
        self.settings = SimpleNamespace(
            throne_parse_test_sends_as_real_sends=False,
            throne_test_gifter_usernames=("marie_123",),
            throne_test_send_leaderboard_owner_user_id=None,
        )
        self.guild_settings_repo = SimpleNamespace(
            get=self._get_settings,
        )
        self.leaderboards_repo = _FakeLeaderboardsRepo()
        self._domme_role_id = domme_role_id
        self._sub_role_id = sub_role_id
        self._leaderboard_view_role_id = leaderboard_view_role_id
        self._mod_role_id = mod_role_id

    async def _get_settings(self, _guild_id: int):
        return SimpleNamespace(
            domme_role_id=self._domme_role_id,
            sub_role_id=self._sub_role_id,
            leaderboard_view_role_id=self._leaderboard_view_role_id,
            mod_role_id=self._mod_role_id,
        )


def _message_text(payload: dict) -> str:
    view = payload["view"]
    return "\n".join(
        str(getattr(item, "content", ""))
        for container in view.children
        for item in getattr(container, "children", [])
    )


def test_leaderboard_is_not_ephemeral_for_member_with_both_roles():
    viewer = _FakeMember(user_id=10, display_name="Pat", role_ids=[11, 22])
    guild = _FakeGuild({10: viewer, 20: _FakeMember(user_id=20, display_name="Alex", role_ids=[22]), 30: _FakeMember(user_id=30, display_name="Sam", role_ids=[11])})
    interaction = _FakeInteraction(user=viewer, guild=guild)
    cog = LeaderboardsCog(_FakeBot(domme_role_id=11, sub_role_id=22))

    asyncio.run(LeaderboardsCog.leaderboard.callback(cog, interaction, user=None))

    payload = interaction.response.messages[0]
    assert payload["ephemeral"] is False
    text = _message_text(payload)
    assert "Send Stats | Dom/me" in text
    assert "Send Stats | Sub" in text
    assert "👑 #1" in text


def test_leaderboard_can_show_another_members_role_based_stats():
    viewer = _FakeMember(user_id=99, display_name="Viewer", role_ids=[])
    target = _FakeMember(user_id=20, display_name="Alex", role_ids=[22])
    guild = _FakeGuild({99: viewer, 20: target, 30: _FakeMember(user_id=30, display_name="Sam", role_ids=[11])})
    interaction = _FakeInteraction(user=viewer, guild=guild)
    cog = LeaderboardsCog(_FakeBot(domme_role_id=11, sub_role_id=22))

    asyncio.run(LeaderboardsCog.leaderboard.callback(cog, interaction, user=target))

    text = _message_text(interaction.response.messages[0])
    assert "Alex's Send Stats | Sub" in text
    assert "Send Stats | Dom/me" not in text


def test_leaderboard_member_without_roles_gets_role_guidance():
    viewer = _FakeMember(user_id=999, display_name="NoRoles", role_ids=[])
    guild = _FakeGuild({999: viewer})
    interaction = _FakeInteraction(user=viewer, guild=guild)
    cog = LeaderboardsCog(_FakeBot(domme_role_id=11, sub_role_id=22))

    asyncio.run(LeaderboardsCog.leaderboard.callback(cog, interaction, user=None))

    payload = interaction.response.messages[0]
    assert payload["ephemeral"] is False
    text = _message_text(payload)
    assert "could not find Dom/me or Sub roles" in text


def _deep_text(payload: dict) -> str:
    view = payload["view"]
    parts: list[str] = []
    for item in view.walk_children():
        content = getattr(item, "content", None)
        if content:
            parts.append(str(content))
    return "\n".join(parts)


def test_leaderboard_blocked_in_test_guild_without_access_role():
    viewer = _FakeMember(user_id=10, display_name="Pat", role_ids=[11])
    guild = _FakeGuild({10: viewer}, guild_id=TEST_GUILD_ID)
    interaction = _FakeInteraction(user=viewer, guild=guild)
    cog = LeaderboardsCog(
        _FakeBot(domme_role_id=11, sub_role_id=22, leaderboard_view_role_id=500)
    )

    asyncio.run(LeaderboardsCog.leaderboard.callback(cog, interaction, user=None))

    payload = interaction.response.messages[0]
    assert payload["ephemeral"] is True
    assert "members-only" in _deep_text(payload)


def test_leaderboard_allowed_in_test_guild_with_access_role():
    viewer = _FakeMember(user_id=10, display_name="Pat", role_ids=[500, 11])
    guild = _FakeGuild({10: viewer}, guild_id=TEST_GUILD_ID)
    interaction = _FakeInteraction(user=viewer, guild=guild)
    cog = LeaderboardsCog(
        _FakeBot(domme_role_id=11, sub_role_id=22, leaderboard_view_role_id=500)
    )

    asyncio.run(LeaderboardsCog.leaderboard.callback(cog, interaction, user=None))

    payload = interaction.response.messages[0]
    assert payload["ephemeral"] is False
    assert "Send Stats | Dom/me" in _message_text(payload)


def test_leaderboard_open_in_test_guild_when_no_access_role_configured():
    viewer = _FakeMember(user_id=10, display_name="Pat", role_ids=[11])
    guild = _FakeGuild({10: viewer}, guild_id=TEST_GUILD_ID)
    interaction = _FakeInteraction(user=viewer, guild=guild)
    cog = LeaderboardsCog(
        _FakeBot(domme_role_id=11, sub_role_id=22, leaderboard_view_role_id=None)
    )

    asyncio.run(LeaderboardsCog.leaderboard.callback(cog, interaction, user=None))

    payload = interaction.response.messages[0]
    assert payload["ephemeral"] is False
    assert "Send Stats | Dom/me" in _message_text(payload)


def test_leaderboard_blocked_in_main_guild_without_access_role():
    viewer = _FakeMember(user_id=10, display_name="Pat", role_ids=[11])
    guild = _FakeGuild({10: viewer}, guild_id=MAIN_GUILD_ID)
    interaction = _FakeInteraction(user=viewer, guild=guild)
    cog = LeaderboardsCog(
        _FakeBot(domme_role_id=11, sub_role_id=22, leaderboard_view_role_id=500)
    )

    asyncio.run(LeaderboardsCog.leaderboard.callback(cog, interaction, user=None))

    payload = interaction.response.messages[0]
    assert payload["ephemeral"] is True
    assert "members-only" in _deep_text(payload)


def test_leaderboard_allowed_in_main_guild_with_access_role():
    viewer = _FakeMember(user_id=10, display_name="Pat", role_ids=[500, 11])
    guild = _FakeGuild({10: viewer}, guild_id=MAIN_GUILD_ID)
    interaction = _FakeInteraction(user=viewer, guild=guild)
    cog = LeaderboardsCog(
        _FakeBot(domme_role_id=11, sub_role_id=22, leaderboard_view_role_id=500)
    )

    asyncio.run(LeaderboardsCog.leaderboard.callback(cog, interaction, user=None))

    payload = interaction.response.messages[0]
    assert payload["ephemeral"] is False
    assert "Send Stats | Dom/me" in _message_text(payload)


def test_leaderboard_open_in_non_system_guild_even_with_access_role():
    viewer = _FakeMember(user_id=10, display_name="Pat", role_ids=[11])
    guild = _FakeGuild({10: viewer}, guild_id=OTHER_GUILD_ID)
    interaction = _FakeInteraction(user=viewer, guild=guild)
    cog = LeaderboardsCog(
        _FakeBot(domme_role_id=11, sub_role_id=22, leaderboard_view_role_id=500)
    )

    asyncio.run(LeaderboardsCog.leaderboard.callback(cog, interaction, user=None))

    payload = interaction.response.messages[0]
    assert payload["ephemeral"] is False
    assert "Send Stats | Dom/me" in _message_text(payload)
