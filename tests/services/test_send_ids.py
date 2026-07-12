from __future__ import annotations

from datetime import datetime, timezone

from rob.database.repositories.models import SendRecord
from rob.utils.send_ids import build_public_send_id, generate_public_send_id


def _send(send_id: int, *, guild_id: int = 1, event_id: str | None = "evt_1") -> SendRecord:
    now = datetime.now(timezone.utc)
    return SendRecord(
        send_id,
        guild_id,
        None,
        10,
        None,
        20,
        "gifter_name",
        1099,
        "USD",
        None,
        "throne_webhook",
        "Flowers",
        None,
        None,
        event_id,
        "hash_1",
        False,
        False,
        now,
        now,
        "posted",
        None,
        None,
        None,
        now,
    )


def test_generated_public_send_id_matches_expected_format():
    public_id = generate_public_send_id(151, guild_id=2, stable_seed="evt_151")
    assert public_id.startswith("ROB-000151-")
    assert len(public_id.split("-")[2]) == 8


def test_generated_public_send_id_is_stable_for_same_input():
    first = generate_public_send_id(151, guild_id=2, stable_seed="evt_151")
    second = generate_public_send_id(151, guild_id=2, stable_seed="evt_151")
    assert first == second


def test_generated_public_send_id_is_unique_for_different_sends():
    first = generate_public_send_id(151, guild_id=2, stable_seed="evt_151")
    second = generate_public_send_id(152, guild_id=2, stable_seed="evt_152")
    assert first != second


def test_build_public_send_id_uses_stable_send_fields():
    send = _send(151)
    assert build_public_send_id(send).startswith("ROB-000151-")
