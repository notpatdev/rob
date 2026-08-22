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
