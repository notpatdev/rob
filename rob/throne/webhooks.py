from __future__ import annotations

import json
import logging
from typing import Any

from aiohttp import web

from rob.config.guilds import is_new_system_guild
from rob.config.settings import WebhookSettings
from rob.database.connection import Database
from rob.database.repositories.bot_state import BotStateRepository
from rob.database.repositories.dommes import DommesRepository
from rob.database.repositories.sends import SendsRepository
from rob.services.maintenance_service import MaintenanceService
from rob.opsapi.notify_client import (
    notify_bot_onboarding_webhook_verified,
    notify_bot_send,
)
from rob.services.send_service import SendService
from rob.services.throne_service import ThroneService
from rob.throne.payloads import is_explicit_test_webhook_payload, is_known_test_sender, is_supported_event_type, parse_throne_send_payload
from rob.throne.security import build_signed_message, secret_matches, validate_timestamp_header, verify_ed25519_signature

log = logging.getLogger(__name__)


async def handle_health(request: web.Request) -> web.Response:
    return web.Response(text="OK")


async def handle_throne_webhook(request: web.Request) -> web.Response:
    database: Database = request.app["database"]
    settings: WebhookSettings = request.app["settings"]
    throne: ThroneService = request.app["throne_service"]

    creator_id = request.match_info["creator_id"]
    provided_secret = request.match_info["secret"]

    raw_body = await request.read()

    timestamp_header = request.headers.get(settings.throne_webhook_timestamp_header)
    signature_header = request.headers.get(settings.throne_webhook_signature_header, "").strip()

    if settings.throne_webhook_require_signature:
        if not validate_timestamp_header(
            timestamp_header,
            max_skew_seconds=settings.throne_webhook_max_timestamp_skew_seconds,
        ):
            return web.json_response({"ok": False, "error": "invalid_timestamp"}, status=401)
        if not settings.throne_public_key_pem:
            return web.json_response({"ok": False, "error": "signature_not_configured"}, status=401)
        message = build_signed_message(
            timestamp=timestamp_header or "",
            raw_body=raw_body,
            signed_message_format=settings.throne_webhook_signed_message_format,
        )
        if not verify_ed25519_signature(
            public_key_pem=settings.throne_public_key_pem,
            signature_hex=signature_header,
            message=message,
        ):
            return web.json_response({"ok": False, "error": "invalid_signature"}, status=401)

    try:
        payload: dict[str, Any] = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return web.json_response({"ok": False, "error": "invalid_json"}, status=400)

    if settings.throne_webhook_debug_log_payload:
        log.info("Throne webhook payload for %s: %s", creator_id, payload)

    dommes = DommesRepository(database)
    matching_creators = await dommes.get_by_creator_id(creator_id)

    matched_creator = None
    for creator in matching_creators:
        if secret_matches(
            provided_secret=provided_secret,
            stored_secret=creator.webhook_secret,
            stored_secret_hash=creator.webhook_secret_hash,
        ):
            matched_creator = creator
            break

    if matched_creator is None:
        return web.json_response(
            {"ok": False, "error": "creator_not_found_or_secret_invalid"},
            status=403,
        )

    # Wind-down phase 2+: stop recording Throne sends entirely. Ack with 200 so
    # Throne stops retrying. This is what functionally invalidates the URLs.
    if await MaintenanceService(BotStateRepository(database)).get_wind_down_phase() >= 2:
        return web.json_response({"ok": True, "ignored": True, "reason": "wind_down"})

    parsed = parse_throne_send_payload(creator_id=creator_id, payload=payload)
    explicit_test = is_explicit_test_webhook_payload(payload, parsed)
    known_test_sender = is_known_test_sender(parsed.gifter_username, test_gifter_usernames=set(settings.throne_test_gifter_usernames))
    in_new_system_guild = is_new_system_guild(matched_creator.guild_id)
    log.info(
        "Throne webhook received creator_id=%s guild_id=%s discord_user_id=%s "
        "event_type=%s gifter=%s explicit_test=%s known_test_sender=%s "
        "parse_test_as_real=%s new_system_guild=%s",
        creator_id,
        matched_creator.guild_id,
        matched_creator.discord_user_id,
        parsed.event_type,
        parsed.gifter_username,
        explicit_test,
        known_test_sender,
        settings.throne_parse_test_sends_as_real_sends,
        in_new_system_guild,
    )

    notify_state: dict[str, bool] = {"sent": False}

    async def _maybe_auto_advance(reason: str) -> None:
        """Notify the bot to advance DM onboarding, at most once per webhook.

        Safe to call repeatedly (the cog gates on onboarding stage); failures
        must not break the webhook response.
        """

        if not in_new_system_guild:
            log.info(
                "Onboarding auto-advance skipped (not new-system guild) "
                "guild_id=%s discord_user_id=%s reason=%s",
                matched_creator.guild_id,
                matched_creator.discord_user_id,
                reason,
            )
            return
        if notify_state["sent"]:
            log.info(
                "Onboarding auto-advance already notified for this webhook "
                "guild_id=%s discord_user_id=%s skipped_reason=%s",
                matched_creator.guild_id,
                matched_creator.discord_user_id,
                reason,
            )
            return
        notify_state["sent"] = True
        log.info(
            "Onboarding auto-advance notifying bot guild_id=%s "
            "discord_user_id=%s reason=%s",
            matched_creator.guild_id,
            matched_creator.discord_user_id,
            reason,
        )
        try:
            delivered = await notify_bot_onboarding_webhook_verified(
                notify_base_url=settings.rob_bot_notify_url,
                secret=settings.rob_ops_secret,
                guild_id=int(matched_creator.guild_id),
                discord_user_id=int(matched_creator.discord_user_id),
            )
        except Exception:
            log.exception(
                "Onboarding auto-advance notification raised for guild_id=%s "
                "discord_user_id=%s reason=%s",
                matched_creator.guild_id,
                matched_creator.discord_user_id,
                reason,
            )
            return
        log.info(
            "Onboarding auto-advance notification result delivered=%s "
            "guild_id=%s discord_user_id=%s reason=%s",
            delivered,
            matched_creator.guild_id,
            matched_creator.discord_user_id,
            reason,
        )

    if explicit_test:
        log.info(
            "Throne webhook classified as explicit test creator_id=%s",
            creator_id,
        )
        await dommes.mark_setup_verified(matched_creator.id)
        await _maybe_auto_advance("explicit_test")
        return web.json_response(
            {
                "ok": True,
                "setup_verified": True,
            }
        )
    if known_test_sender and not settings.throne_parse_test_sends_as_real_sends:
        log.info(
            "Throne webhook classified as known test sender (setup-only) "
            "creator_id=%s gifter=%s",
            creator_id,
            parsed.gifter_username,
        )
        await dommes.mark_setup_verified(matched_creator.id)
        await _maybe_auto_advance("known_test_sender")
        # Fall through: these rows are still inserted via record_throne_send so
        # the send tracker can render them (is_test_send=true keeps them off
        # leaderboards).
    if known_test_sender and settings.throne_parse_test_sends_as_real_sends:
        log.warning("Known Throne test sender accepted as real send due to THRONE_PARSE_TEST_SENDS_AS_REAL_SENDS=true. creator_id=%s gifter_username=%s", creator_id, parsed.gifter_username)
        # Same Throne test click, just stored as a real send; still mark setup
        # and fall through to record_throne_send.
        await dommes.mark_setup_verified(matched_creator.id)
        await _maybe_auto_advance("known_test_sender_parsed_as_real")

    if not is_supported_event_type(parsed.event_type):
        await dommes.touch_successful_event(matched_creator.id)
        return web.json_response(
            {
                "ok": True,
                "ignored": True,
                "event_type": parsed.event_type,
            }
        )

    maintenance = MaintenanceService(BotStateRepository(database))
    send_service = SendService(
        sends=SendsRepository(database),
        subs=request.app["subs_repository"],
        maintenance=maintenance,
        throne=throne,
        throne_test_gifter_usernames=settings.throne_test_gifter_usernames,
    )
    send = await send_service.record_throne_send(
        creator=matched_creator,
        payload=parsed,
    )
    await dommes.touch_successful_event(matched_creator.id)

    if send is None:
        # Onboarding may be waiting on this same webhook, duplicate or not.
        await _maybe_auto_advance("duplicate_send")
        return web.json_response({"ok": True, "duplicate": True})

    bot_notified = await notify_bot_send(
        notify_url=settings.rob_bot_notify_url,
        secret=settings.rob_ops_secret,
        send_id=send.id,
        guild_id=send.guild_id,
    )

    # A real send is the same signal a mid-onboarding Dom/me is waiting for;
    # the cog is a no-op once onboarding is complete.
    await _maybe_auto_advance("real_send_recorded")

    response: dict[str, Any] = {
        "ok": True,
        "inserted": True,
        "send_id": send.id,
        "bot_notified": bot_notified,
    }
    if known_test_sender and not settings.throne_parse_test_sends_as_real_sends:
        response["setup_verified"] = True
    return web.json_response(response)


def create_webhook_app(
    *,
    settings: WebhookSettings,
    database: Database,
) -> web.Application:
    from rob.database.repositories.subs import SubsRepository

    app = web.Application()
    app["settings"] = settings
    app["database"] = database
    app["throne_service"] = ThroneService()
    app["subs_repository"] = SubsRepository(database)
    app.router.add_get("/health", handle_health)
    app.router.add_post("/throne/webhook/{creator_id}/{secret}", handle_throne_webhook)
    app.router.add_post("/webhook/{creator_id}/{secret}", handle_throne_webhook)

    async def close_throne_service(_app: web.Application) -> None:
        await _app["throne_service"].close()

    app.on_cleanup.append(close_throne_service)
    return app
