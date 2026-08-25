from __future__ import annotations

import logging
from urllib.parse import urlsplit, urlunsplit

from aiohttp import ClientError, ClientSession, ClientTimeout

log = logging.getLogger(__name__)


def _derive_bot_ops_endpoint(
    *,
    notify_base_url: str | None,
    path: str,
) -> str | None:
    if not notify_base_url:
        return None
    try:
        parts = urlsplit(notify_base_url)
    except ValueError:
        return None
    if not parts.scheme or not parts.netloc:
        return None
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


async def notify_bot_send(
    *,
    notify_url: str | None,
    secret: str | None,
    send_id: int,
    guild_id: int,
    timeout_seconds: float = 5.0,
) -> bool:
    if not notify_url:
        return False

    headers: dict[str, str] = {}
    if secret:
        headers["X-Rob-Ops-Secret"] = secret

    payload = {"send_id": int(send_id), "guild_id": int(guild_id)}
    timeout = ClientTimeout(total=timeout_seconds)
    try:
        async with ClientSession(timeout=timeout) as session:
            async with session.post(notify_url, json=payload, headers=headers) as response:
                if 200 <= response.status < 300:
                    return True
                body = await response.text()
                log.warning(
                    "Bot send notification failed status=%s send_id=%s guild_id=%s response=%s",
                    response.status,
                    send_id,
                    guild_id,
                    body[:200],
                )
                return False
    except (ClientError, TimeoutError):
        log.exception(
            "Bot send notification failed send_id=%s guild_id=%s.",
            send_id,
            guild_id,
        )
        return False


async def notify_bot_onboarding_webhook_verified(
    *,
    notify_base_url: str | None,
    secret: str | None,
    guild_id: int,
    discord_user_id: int,
    timeout_seconds: float = 5.0,
) -> bool:
    """POST to the bot ops server to advance an in-progress DM onboarding
    flow when the Throne test webhook arrives.

    ``notify_base_url`` should be the same base used by the existing
    ``ROB_BOT_NOTIFY_URL`` (e.g. ``http://127.0.0.1:8090/sends/process``);
    this function derives the onboarding endpoint from the same origin.
    """

    endpoint = _derive_bot_ops_endpoint(
        notify_base_url=notify_base_url,
        path="/onboarding/webhook_verified",
    )
    if endpoint is None:
        return False

    headers: dict[str, str] = {}
    if secret:
        headers["X-Rob-Ops-Secret"] = secret

    payload = {
        "guild_id": int(guild_id),
        "discord_user_id": int(discord_user_id),
    }
    timeout = ClientTimeout(total=timeout_seconds)
    log.info(
        "Onboarding webhook-verified notification POST endpoint=%s "
        "guild_id=%s discord_user_id=%s",
        endpoint,
        guild_id,
        discord_user_id,
    )
    try:
        async with ClientSession(timeout=timeout) as session:
            async with session.post(endpoint, json=payload, headers=headers) as response:
                if 200 <= response.status < 300:
                    log.info(
                        "Onboarding webhook-verified notification delivered "
                        "status=%s guild_id=%s discord_user_id=%s",
                        response.status,
                        guild_id,
                        discord_user_id,
                    )
                    return True
                body = await response.text()
                log.warning(
                    "Onboarding webhook-verified notification failed status=%s "
                    "guild_id=%s discord_user_id=%s response=%s",
                    response.status,
                    guild_id,
                    discord_user_id,
                    body[:200],
                )
                return False
    except (ClientError, TimeoutError):
        log.exception(
            "Onboarding webhook-verified notification failed guild_id=%s discord_user_id=%s.",
            guild_id,
            discord_user_id,
        )
        return False

