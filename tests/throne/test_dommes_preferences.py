"""Signature/wiring tests for the new Dom/me preference repository methods.

We don't spin up a Postgres in this test process, but we can verify the
public surface of ``DommesRepository`` exposes the preference methods the
DM-onboarding and ``/settings`` flows depend on.
"""

from __future__ import annotations

import inspect

from rob.database.repositories.dommes import DommesRepository
from rob.database.repositories.models import Domme


def test_domme_dataclass_exposes_new_preference_fields():
    fields = {f for f in Domme.__dataclass_fields__}
    assert {
        "leaderboard_visible",
        "preferences_deferred_until",
        "preferences_confirmed_at",
    }.issubset(fields)


def test_set_preferences_signature_supports_all_flow_kwargs():
    sig = inspect.signature(DommesRepository.set_preferences)
    params = sig.parameters
    for name in (
        "guild_id",
        "discord_user_id",
        "leaderboard_visible",
        "preferences_deferred_until",
        "clear_defer",
        "confirm",
    ):
        assert name in params, f"missing kwarg {name!r}"


def test_defer_helper_exists():
    defer = inspect.signature(DommesRepository.defer_preferences)
    assert "until" in defer.parameters
    assert "guild_id" in defer.parameters and "discord_user_id" in defer.parameters
