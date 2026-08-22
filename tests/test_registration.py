from __future__ import annotations

from bill.cogs.registration import registration_embed
from bill.worker_client import DommeRegistration


def test_new_registration_explains_webhook_setup() -> None:
    embed = registration_embed(
        DommeRegistration(
            creator_id="creator-id",
            throne_handle="alice",
            webhook_url="https://usebill.dev/t/creator-id/secret",
            webhook_state="issued",
        )
    )

    assert "Add Bill" in (embed.title or "")
    assert "Keep this URL private" in (embed.description or "")
    assert "creator-id/secret" in (embed.description or "")


def test_existing_registration_does_not_request_webhook_change() -> None:
    embed = registration_embed(
        DommeRegistration(
            creator_id="creator-id",
            throne_handle="alice",
            webhook_url=None,
            webhook_state="existing",
        )
    )

    assert "nothing to change" in (embed.description or "")
