from __future__ import annotations

from typing import Any

import pytest

from bill.worker_client import WorkerAPIError, WorkerClient


class FakeResponse:
    def __init__(self, *, status: int, payload: Any) -> None:
        self.status = status
        self._payload = payload

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def json(self) -> Any:
        return self._payload


class FakeSession:
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.response


def draft_payload() -> dict[str, object]:
    return {
        "id": "draft-1",
        "owner_user_id": "123",
        "origin_guild_id": "456",
        "target_scope": "global",
        "guild_id": None,
        "server_mode": None,
        "status": "active",
        "revision": 4,
        "base_version": 0,
        "current_step": "throne",
        "next_step": "throne",
        "steps": [{"key": "throne", "status": "pending", "completed_at": None}],
        "dm_status_selected": True,
        "governing_orientation": "domme",
        "document": {
            "dm_status": "open",
            "bio": None,
            "public_send_stats": False,
            "selections": {
                "pronouns": ["She/Her"],
                "honourifics": [],
                "submissive_labels": [],
            },
            "aliases": [],
            "links": [],
            "overridden_fields": [],
            "hidden_inherited_link_ids": [],
            "throne_creator_id": "creator",
            "preferred_payment_link_id": None,
            "profile_color": None,
        },
        "throne_prefill": None,
        "created_at": None,
        "updated_at": None,
        "published_at": None,
        "wizard_stage": "throne",
        "wizard_substep": "awaiting_verification",
        "throne_pending": None,
        "resolved_profile_color": None,
    }


@pytest.mark.asyncio
async def test_configure_guild_serializes_snowflakes_and_auth() -> None:
    session = FakeSession(
        FakeResponse(
            status=200,
            payload={
                "ok": True,
                "data": {"guild_id": "123", "send_channel_id": "456"},
            },
        )
    )
    client = WorkerClient(
        base_url="https://usebill.dev",
        api_token="secret",
        session=session,  # type: ignore[arg-type]
    )

    config = await client.configure_guild(guild_id=123, send_channel_id=456)

    assert config.send_channel_id == "456"
    assert session.calls[0]["json"] == {"send_channel_id": "456"}
    assert session.calls[0]["headers"]["Authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_worker_error_preserves_safe_code_and_status() -> None:
    session = FakeSession(
        FakeResponse(
            status=409,
            payload={
                "ok": False,
                "error": {"code": "creator_owned", "message": "Already linked"},
            },
        )
    )
    client = WorkerClient(
        base_url="https://usebill.dev",
        api_token="secret",
        session=session,  # type: ignore[arg-type]
    )

    with pytest.raises(WorkerAPIError) as caught:
        await client.register_domme(
            guild_id="123",
            discord_user_id="456",
            throne="alice",
            reset_webhook=False,
        )

    assert caught.value.status == 409
    assert caught.value.code == "creator_owned"


@pytest.mark.asyncio
async def test_invalid_snowflake_is_rejected_before_request() -> None:
    session = FakeSession(FakeResponse(status=200, payload={"ok": True, "data": {}}))
    client = WorkerClient(
        base_url="https://usebill.dev",
        api_token="secret",
        session=session,  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="snowflakes"):
        await client.get_guild_config("not-a-number")

    assert session.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ("attach_throne", "rotate_throne"))
async def test_throne_mutations_return_embedded_draft_without_followup_get(
    operation: str,
) -> None:
    session = FakeSession(
        FakeResponse(
            status=200,
            payload={
                "ok": True,
                "data": {
                    "draft": draft_payload(),
                    "webhook_url": "https://usebill.dev/t/creator/one-time",
                    "webhook_state": "rotated",
                },
            },
        )
    )
    client = WorkerClient(
        base_url="https://usebill.dev",
        api_token="secret",
        session=session,  # type: ignore[arg-type]
    )

    result = await getattr(client, operation)(
        "draft-1",
        owner_user_id="123",
        expected_revision=3,
    )

    assert result.draft.id == "draft-1"
    assert result.draft.owner_user_id == "123"
    assert result.draft.document.selections.pronouns == ("She/Her",)
    assert result.webhook_url == "https://usebill.dev/t/creator/one-time"
    assert len(session.calls) == 1


@pytest.mark.asyncio
async def test_throne_resolution_returns_embedded_draft_without_followup_get() -> None:
    payload = draft_payload()
    payload["wizard_substep"] = "review:confirm"
    session = FakeSession(
        FakeResponse(
            status=200,
            payload={
                "ok": True,
                "data": {
                    "draft": payload,
                    "handle": "creator",
                    "already_verified": False,
                },
            },
        )
    )
    client = WorkerClient(
        base_url="https://usebill.dev",
        api_token="secret",
        session=session,  # type: ignore[arg-type]
    )

    result = await client.resolve_throne(
        "draft-1",
        owner_user_id="123",
        expected_revision=3,
        throne_input="creator",
    )

    assert result.draft.wizard_substep == "review:confirm"
    assert result.handle == "creator"
    assert len(session.calls) == 1
