from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import aiohttp


class WorkerAPIError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True, slots=True)
class GuildConfig:
    guild_id: str
    send_channel_id: str


@dataclass(frozen=True, slots=True)
class DommeRegistration:
    creator_id: str
    throne_handle: str
    webhook_url: str | None
    webhook_state: str


@dataclass(frozen=True, slots=True)
class SendNotification:
    notification_id: str
    lease_token: str
    send_id: str
    guild_id: str
    channel_id: str
    recipient_user_id: str
    throne_handle: str
    amount_minor: int
    currency: str
    sender_name: str | None
    is_private: bool
    is_anonymous: bool
    item_name: str | None
    item_image_url: str | None
    purchased_at: str
    delivery_may_exist: bool


def _snowflake(value: int | str) -> str:
    text = str(value)
    if not text.isdecimal():
        raise ValueError("Discord snowflakes must contain only decimal digits")
    return text


class WorkerClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_token: str,
        session: aiohttp.ClientSession,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_token = api_token
        self._session = session
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def get_guild_config(self, guild_id: int | str) -> GuildConfig | None:
        try:
            data = await self._request("GET", f"/v1/guilds/{_snowflake(guild_id)}/config")
        except WorkerAPIError as exc:
            if exc.status == 404:
                return None
            raise
        return GuildConfig(
            guild_id=_snowflake(data["guild_id"]),
            send_channel_id=_snowflake(data["send_channel_id"]),
        )

    async def configure_guild(
        self,
        *,
        guild_id: int | str,
        send_channel_id: int | str,
    ) -> GuildConfig:
        data = await self._request(
            "PUT",
            f"/v1/guilds/{_snowflake(guild_id)}/config",
            json={"send_channel_id": _snowflake(send_channel_id)},
        )
        return GuildConfig(
            guild_id=_snowflake(data["guild_id"]),
            send_channel_id=_snowflake(data["send_channel_id"]),
        )

    async def register_domme(
        self,
        *,
        guild_id: int | str,
        discord_user_id: int | str,
        throne: str,
        reset_webhook: bool,
    ) -> DommeRegistration:
        data = await self._request(
            "POST",
            f"/v1/guilds/{_snowflake(guild_id)}/registrations/domme",
            json={
                "discord_user_id": _snowflake(discord_user_id),
                "throne": throne,
                "reset_webhook": reset_webhook,
            },
        )
        webhook_url = data.get("webhook_url")
        return DommeRegistration(
            creator_id=str(data["creator_id"]),
            throne_handle=str(data["throne_handle"]),
            webhook_url=str(webhook_url) if webhook_url else None,
            webhook_state=str(data["webhook_state"]),
        )

    async def lease_notifications(
        self,
        *,
        owner: str,
        limit: int,
        lease_seconds: int,
    ) -> list[SendNotification]:
        data = await self._request(
            "POST",
            "/v1/notifications/lease",
            json={"owner": owner, "limit": limit, "lease_seconds": lease_seconds},
        )
        rows = data.get("notifications")
        if not isinstance(rows, list):
            raise WorkerAPIError("Worker returned an invalid notification list")
        return [self._parse_notification(row) for row in rows]

    async def ack_notification(
        self,
        notification_id: str,
        *,
        lease_token: str,
        discord_message_id: int | str,
    ) -> None:
        await self._request(
            "POST",
            f"/v1/notifications/{notification_id}/ack",
            json={
                "lease_token": lease_token,
                "discord_message_id": _snowflake(discord_message_id),
            },
        )

    async def nack_notification(
        self,
        notification_id: str,
        *,
        lease_token: str,
        error: str,
        permanent: bool,
    ) -> None:
        await self._request(
            "POST",
            f"/v1/notifications/{notification_id}/nack",
            json={
                "lease_token": lease_token,
                "error": error[:300],
                "permanent": permanent,
            },
        )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._api_token}",
            "Accept": "application/json",
        }
        try:
            async with self._session.request(
                method,
                f"{self._base_url}{path}",
                headers=headers,
                json=json,
                timeout=self._timeout,
            ) as response:
                try:
                    payload = await response.json()
                except (aiohttp.ContentTypeError, ValueError) as exc:
                    raise WorkerAPIError(
                        "Worker returned a non-JSON response",
                        status=response.status,
                    ) from exc
        except TimeoutError as exc:
            raise WorkerAPIError("Worker request timed out") from exc
        except aiohttp.ClientError as exc:
            raise WorkerAPIError("Worker request failed") from exc

        if not isinstance(payload, dict):
            raise WorkerAPIError("Worker returned an invalid response", status=response.status)
        if response.status >= 400 or payload.get("ok") is not True:
            error = payload.get("error")
            if isinstance(error, dict):
                code = str(error.get("code", "worker_error"))
                message = str(error.get("message", "Worker request failed"))
            else:
                code = str(payload.get("code", "worker_error"))
                message = str(error or payload.get("message") or "Worker request failed")
            raise WorkerAPIError(message, status=response.status, code=code)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise WorkerAPIError(
                "Worker response did not contain an object",
                status=response.status,
            )
        return data

    @staticmethod
    def _parse_notification(value: Any) -> SendNotification:
        if not isinstance(value, dict):
            raise WorkerAPIError("Worker returned an invalid notification")
        try:
            amount_minor = int(value["amount_minor"])
        except (KeyError, TypeError, ValueError) as exc:
            raise WorkerAPIError("Worker returned an invalid notification amount") from exc
        return SendNotification(
            notification_id=str(value["notification_id"]),
            lease_token=str(value["lease_token"]),
            send_id=str(value["send_id"]),
            guild_id=_snowflake(value["guild_id"]),
            channel_id=_snowflake(value["channel_id"]),
            recipient_user_id=_snowflake(value["recipient_user_id"]),
            throne_handle=str(value["throne_handle"]),
            amount_minor=amount_minor,
            currency=str(value["currency"]),
            sender_name=str(value["sender_name"]) if value.get("sender_name") else None,
            is_private=bool(value.get("is_private")),
            is_anonymous=bool(value.get("is_anonymous")),
            item_name=str(value["item_name"]) if value.get("item_name") else None,
            item_image_url=(
                str(value["item_image_url"]) if value.get("item_image_url") else None
            ),
            purchased_at=str(value["purchased_at"]),
            delivery_may_exist=bool(value.get("delivery_may_exist")),
        )
