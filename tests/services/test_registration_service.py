from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from rob.services.registration_service import RegistrationService


class _FakeGuildSettings:
    async def ensure_guild(self, guild_id: int):
        return guild_id


class _FakeSubs:
    def __init__(self):
        self.calls: list[dict] = []

    async def upsert_with_send_names(self, *, guild_id: int, discord_user_id: int, send_names: list[str]):
        self.calls.append(
            {
                "guild_id": guild_id,
                "discord_user_id": discord_user_id,
                "send_names": list(send_names),
            }
        )
        return SimpleNamespace(
            id=1,
            guild_id=guild_id,
            discord_user_id=discord_user_id,
            send_name=send_names[0],
        )


class _FakeDommes:
    async def upsert(self, **kwargs):
        return SimpleNamespace(id=1, **kwargs)

    async def get_by_creator_id(self, _creator_id: str):
        return []


class _FakeBlacklist:
    async def contains(self, _discord_user_id: int):
        return False


class _FakeThrone:
    def normalize_profile(self, value: str):
        return SimpleNamespace(profile_url=value, handle="handle", creator_id="creator")


def _service(subs: _FakeSubs) -> RegistrationService:
    return RegistrationService(
        guild_settings=_FakeGuildSettings(),
        dommes=_FakeDommes(),
        subs=subs,
        blacklist=_FakeBlacklist(),
        throne=_FakeThrone(),
        webhook_base_url="https://throne.robthebot.com",
    )


def test_register_sub_rejects_duplicate_names():
    service = _service(_FakeSubs())
    with pytest.raises(ValueError, match="Duplicate sending names"):
        asyncio.run(
            service.register_sub(
                guild_id=1,
                discord_user_id=10,
                send_names=["gifter", "GIFTER"],
            )
        )


def test_register_sub_rejects_reserved_names():
    service = _service(_FakeSubs())
    with pytest.raises(ValueError, match="reserved"):
        asyncio.run(
            service.register_sub(
                guild_id=1,
                discord_user_id=10,
                send_names=["anonymous"],
            )
        )


def test_register_sub_accepts_up_to_three_names_and_persists_aliases():
    subs = _FakeSubs()
    service = _service(subs)

    result = asyncio.run(
        service.register_sub(
            guild_id=1,
            discord_user_id=10,
            send_names=["gifter_one", "gifter_two", "gifter_three"],
        )
    )

    assert result.send_names == ("gifter_one", "gifter_two", "gifter_three")
    assert subs.calls[0]["send_names"] == ["gifter_one", "gifter_two", "gifter_three"]


def test_register_sub_single_name_path_still_supported():
    subs = _FakeSubs()
    service = _service(subs)

    result = asyncio.run(
        service.register_sub(
            guild_id=1,
            discord_user_id=10,
            send_name="gifter_one",
        )
    )

    assert result.send_names == ("gifter_one",)
    assert subs.calls[0]["send_names"] == ["gifter_one"]
