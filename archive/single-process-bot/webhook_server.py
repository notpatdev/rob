"""Throne webhook HTTP server for Rob the Bot.

Provides a POST endpoint that receives signed Throne gift events and inserts
them into the database, then fires Discord notifications.

Route: POST /throne/webhook/{creator_id}/{secret}
Health: GET /health
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

import aiohttp
import discord
from aiohttp import web

from bot.config import BotConfig
from bot.database import Database, ThroneWishlistItem
from bot.event_views import BroadcastNotificationView
from bot.throne_scraper import (
    WishlistItemRecord,
    fetch_public_wishlist_items,
    match_wishlist_item_price,
)
from bot.utils import normalize_sender_name

if TYPE_CHECKING:
    from discord.ext import commands

log = logging.getLogger(__name__)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _compute_fallback_hash(
    creator_id: str,
    order_id: str | None,
    purchased_at: str | None,
    gifter_username: str | None,
    item_name: str | None,
    amount_cents: int | None,
    currency: str | None,
) -> str:
    parts = [
        creator_id,
        order_id or "",
        purchased_at or "",
        gifter_username or "",
        item_name or "",
        str(amount_cents) if amount_cents is not None else "",
        currency or "",
    ]
    raw = "|".join(parts).encode()
    return hashlib.sha256(raw).hexdigest()


def _extract_gift_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract normalised gift fields from a Throne webhook payload.

    Supports both the documented Throne webhook envelope where event-specific
    values live under ``payload["data"]`` and older / alternate top-level
    shapes. Never raises on missing fields.
    """

    def _as_dict(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def _first(*values: Any) -> Any:
        for value in values:
            if value is not None:
                return value
        return None

    def _first_str(*values: Any) -> str | None:
        value = _first(*values)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    def _truthy(*values: Any) -> bool:
        for value in values:
            if bool(value):
                return True
        return False

    data = _as_dict(payload.get("data"))

    # Top-level envelope fields
    event_id: str | None = _first_str(
        payload.get("id"),
        payload.get("eventId"),
        payload.get("event_id"),
    )
    event_type: str | None = _first_str(
        payload.get("type"),
        payload.get("eventType"),
        payload.get("event_type"),
    )

    # Mixed top-level / nested fields
    order_id: str | None = _first_str(
        data.get("orderId"),
        data.get("order_id"),
        payload.get("orderId"),
        payload.get("order_id"),
    )
    status: str | None = _first_str(
        data.get("status"),
        payload.get("status"),
    )
    message: str | None = _first_str(
        data.get("message"),
        payload.get("message"),
    )

    purchased_at: str | None = _first_str(
        data.get("purchasedAt"),
        data.get("purchased_at"),
        data.get("createdAt"),
        data.get("created_at"),
        data.get("timestamp"),
        payload.get("purchasedAt"),
        payload.get("purchased_at"),
        payload.get("createdAt"),
        payload.get("created_at"),
        payload.get("timestamp"),
    )

    # Sender / gifter info
    gifter_obj: dict[str, Any] = {}
    for container in (data, payload):
        for key in ("gifter", "sender", "user"):
            value = container.get(key)
            if isinstance(value, dict):
                gifter_obj = value
                break
        if gifter_obj:
            break

    gifter_username: str | None = _first_str(
        gifter_obj.get("username"),
        gifter_obj.get("name"),
        data.get("gifterUsername"),
        data.get("gifter_username"),
        data.get("senderUsername"),
        data.get("sender_username"),
        data.get("senderName"),
        data.get("sender_name"),
        payload.get("gifterUsername"),
        payload.get("gifter_username"),
        payload.get("senderUsername"),
        payload.get("sender_username"),
        payload.get("senderName"),
        payload.get("sender_name"),
    )

    is_anonymous: bool = _truthy(
        gifter_obj.get("isAnonymous"),
        data.get("isAnonymous"),
        data.get("is_anonymous"),
        data.get("anonymous"),
        payload.get("isAnonymous"),
        payload.get("is_anonymous"),
        payload.get("anonymous"),
    )
    if is_anonymous:
        gifter_username = None

    # Item info
    item_obj: dict[str, Any] = {}
    for container in (data, payload):
        for key in ("gift", "item", "product", "wishlistItem"):
            value = container.get(key)
            if isinstance(value, dict):
                item_obj = value
                break
        if item_obj:
            break

    item_name: str | None = _first_str(
        item_obj.get("name"),
        item_obj.get("title"),
        data.get("itemName"),
        data.get("item_name"),
        payload.get("itemName"),
        payload.get("item_name"),
        payload.get("productName"),
        payload.get("product_name"),
        payload.get("giftName"),
        payload.get("gift_name"),
    )

    item_image_url: str | None = _first_str(
        item_obj.get("imageUrl"),
        item_obj.get("image_url"),
        item_obj.get("image"),
        data.get("itemThumbnailUrl"),
        data.get("item_thumbnail_url"),
        data.get("itemImageUrl"),
        data.get("item_image_url"),
        payload.get("itemThumbnailUrl"),
        payload.get("item_thumbnail_url"),
        payload.get("itemImageUrl"),
        payload.get("item_image_url"),
        payload.get("imageUrl"),
        payload.get("image_url"),
    )
    if item_image_url and not item_image_url.lower().startswith(
        ("http://", "https://")
    ):
        item_image_url = None

    currency: str | None = _first_str(
        data.get("currency"),
        payload.get("currency"),
        item_obj.get("currency"),
    )

    amount_cents: int | None = None
    amount_usd: float | None = None

    raw_cents = _first(
        data.get("amountCents"),
        data.get("amount_cents"),
        payload.get("amountCents"),
        payload.get("amount_cents"),
        payload.get("priceCents"),
        payload.get("price_cents"),
        item_obj.get("amountCents"),
        item_obj.get("amount_cents"),
    )

    if raw_cents is not None:
        try:
            amount_cents = int(raw_cents)
            amount_usd = amount_cents / 100.0
        except (TypeError, ValueError):
            pass

    if amount_usd is None:
        raw_amount = _first(
            data.get("amount"),
            payload.get("amount"),
            item_obj.get("amount"),
            data.get("amountUsd"),
            data.get("amount_usd"),
            data.get("priceUsd"),
            data.get("price_usd"),
            payload.get("amountUsd"),
            payload.get("amount_usd"),
            payload.get("priceUsd"),
            payload.get("price_usd"),
            item_obj.get("amountUsd"),
            item_obj.get("amount_usd"),
            item_obj.get("priceUsd"),
            item_obj.get("price_usd"),
        )

        if raw_amount is not None:
            try:
                if isinstance(raw_amount, str):
                    cleaned = raw_amount.strip().replace("$", "").replace(",", "")
                    if cleaned:
                        numeric_amount = float(cleaned)
                    else:
                        numeric_amount = None
                else:
                    numeric_amount = float(raw_amount)

                if numeric_amount is not None:
                    # Throne docs: contribution_purchased.data.amount is always
                    # in minor units (cents), regardless of currency.
                    # gift_purchased / item_purchased send major-unit USD values.
                    if event_type == "contribution_purchased":
                        amount_cents = int(round(numeric_amount))
                        amount_usd = amount_cents / 100.0
                    else:
                        amount_usd = numeric_amount
                        if amount_cents is None:
                            amount_cents = int(round(numeric_amount * 100))
            except (TypeError, ValueError):
                pass

    is_private: bool = _truthy(
        data.get("isPrivate"),
        data.get("is_private"),
        data.get("amountHidden"),
        data.get("hideAmount"),
        payload.get("isPrivate"),
        payload.get("is_private"),
        payload.get("amountHidden"),
        payload.get("hideAmount"),
    )
    if is_private:
        amount_usd = None

    return {
        "event_id": event_id,
        "event_type": event_type,
        "order_id": order_id,
        "status": status,
        "message": message,
        "purchased_at": purchased_at,
        "gifter_username": gifter_username,
        "is_anonymous": is_anonymous,
        "item_name": item_name,
        "item_image_url": item_image_url,
        "amount_cents": amount_cents,
        "amount_usd": amount_usd,
        "currency": currency,
        "is_private": is_private,
    }


def _verify_ed25519(
    public_key_pem: str,
    signature_hex: str,
    message: bytes,
) -> bool:
    """Verify an Ed25519 signature. Returns False on any error."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        from cryptography.hazmat.primitives.serialization import load_pem_public_key

        signature_bytes = bytes.fromhex(signature_hex)
        if len(signature_bytes) != 64:
            return False
        public_key = load_pem_public_key(public_key_pem.encode())
        if not isinstance(public_key, Ed25519PublicKey):
            log.warning("THRONE_PUBLIC_KEY_PEM is not an Ed25519 public key.")
            return False
        public_key.verify(signature_bytes, message)
        return True
    except Exception:
        return False


class ThroneWebhookServer:
    """Lifecycle-managed aiohttp server for Throne webhook events."""

    def __init__(
        self,
        bot: commands.Bot,
        config: BotConfig,
        database: Database,
    ) -> None:
        self.bot = bot
        self.config = config
        self.database = database
        self._http: aiohttp.ClientSession | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

        if config.throne_webhook_require_signature and not config.throne_public_key_pem:
            log.error(
                "THRONE_PUBLIC_KEY_PEM is required when THRONE_WEBHOOK_REQUIRE_SIGNATURE=true. "
                "Webhook signature verification is enabled but no public key is set. "
                "All webhook requests will be rejected with 401."
            )
        if (
            not config.throne_webhook_require_signature
            and not config.throne_public_key_pem
        ):
            log.warning(
                "⚠️  THRONE_WEBHOOK_REQUIRE_SIGNATURE=false and THRONE_PUBLIC_KEY_PEM is unset. "
                "Webhook signature verification is DISABLED. This is insecure — for local testing only."
            )

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/health", self._handle_health)
        app.router.add_post(
            "/throne/webhook/{creator_id}/{secret}", self._handle_webhook
        )
        app.router.add_post("/admin/maintenance", self._handle_admin_maintenance)
        app.router.add_post(
            "/admin/leaderboard/sync", self._handle_admin_leaderboard_sync
        )
        app.router.add_post("/admin/broadcast", self._handle_admin_broadcast)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        self._site = web.TCPSite(
            self._runner,
            host="127.0.0.1",
            port=self.config.throne_webhook_port,
        )
        await self._site.start()
        log.info(
            "Throne webhook server listening on 127.0.0.1:%s",
            self.config.throne_webhook_port,
        )
        if self.config.throne_webhook_debug_log_payload:
            log.warning(
                "THRONE_WEBHOOK_DEBUG_LOG_PAYLOAD=true. Raw webhook bodies and parsed payload summaries "
                "will be written to logs and may include gift details or user data. Enable only temporarily."
            )

    async def stop(self) -> None:
        if self._http is not None:
            await self._http.close()
            self._http = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            self._site = None
            log.info("Throne webhook server stopped.")

    async def _get_http(self) -> aiohttp.ClientSession:
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession()
        return self._http

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.Response(text="OK")

    async def _handle_webhook(self, request: web.Request) -> web.Response:
        creator_id: str = request.match_info["creator_id"]
        url_secret: str = request.match_info["secret"]

        # 1. Read raw body bytes — must happen before any JSON parsing.
        raw_body: bytes = await request.read()
        config = self.config
        if config.throne_webhook_debug_log_payload:
            log.info(
                "Webhook debug payload for creator %s: %s",
                creator_id,
                raw_body.decode("utf-8", errors="replace"),
            )

        # 2 & 3. Timestamp verification.
        ts_header = request.headers.get(config.throne_webhook_timestamp_header, "")
        ts_stripped = ts_header.strip()
        if not ts_stripped.isdigit():
            log.debug("Webhook rejected: missing or non-numeric timestamp header.")
            return web.Response(status=401, text="Missing or invalid timestamp")
        ts_value = int(ts_stripped)
        now_ts = int(time.time())
        skew = abs(now_ts - ts_value)
        if skew > config.throne_webhook_max_timestamp_skew_seconds:
            log.debug("Webhook rejected: timestamp skew %ss exceeds limit.", skew)
            return web.Response(status=401, text="Timestamp out of range")

        # 4 & 5. Signature verification.
        sig_hex = request.headers.get(
            config.throne_webhook_signature_header, ""
        ).strip()

        if config.throne_webhook_require_signature:
            if not config.throne_public_key_pem:
                log.error(
                    "Webhook rejected: signature required but THRONE_PUBLIC_KEY_PEM not set."
                )
                return web.Response(
                    status=401, text="Signature verification not configured"
                )

            # Build the message to verify based on configured format.
            fmt = config.throne_webhook_signed_message_format
            if fmt == "timestamp_dot_body":
                message_to_verify = f"{ts_header}.".encode() + raw_body
            elif fmt == "timestamp_concat_body":
                message_to_verify = ts_header.encode() + raw_body
            else:
                # "body_only" or any unknown value — use raw body.
                message_to_verify = raw_body

            if not _verify_ed25519(
                config.throne_public_key_pem, sig_hex, message_to_verify
            ):
                log.warning(
                    "Webhook rejected: Ed25519 signature verification failed for creator %s.",
                    creator_id,
                )
                return web.Response(status=401, text="Invalid signature")

        # 6. Look up throne_creators row by creator_id.
        rows = await self.database.get_throne_creators_by_creator_id(
            throne_creator_id=creator_id
        )
        if not rows:
            log.debug("Webhook: no throne_creators row for creator_id=%s", creator_id)
            return web.Response(status=404, text="Creator not found")

        # 7. Find the row whose webhook_secret matches the URL secret.
        matched_row = None
        for row in rows:
            if hmac.compare_digest(url_secret, row.webhook_secret):
                matched_row = row
                break
        if matched_row is None:
            log.warning("Webhook: secret mismatch for creator_id=%s", creator_id)
            return web.Response(status=403, text="Forbidden")

        # 8. Parse JSON payload.
        try:
            payload: dict[str, Any] = json.loads(raw_body.decode())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            log.warning("Webhook: bad JSON from creator %s: %s", creator_id, exc)
            return web.Response(status=400, text="Invalid JSON")

        # 9. Extract gift fields.
        fields = _extract_gift_fields(payload)
        if config.throne_webhook_debug_log_payload:
            log.info(
                "Webhook debug summary for creator %s: event_id=%r event_type=%r data=%s fields=%s",
                creator_id,
                fields["event_id"],
                fields["event_type"],
                payload.get("data"),
                fields,
            )

        event_type = fields["event_type"] or ""
        # Only process purchase events; ignore pings / test events gracefully.
        accepted_types = {
            "gift_purchased",
            "contribution_purchased",
            "gift_crowdfunded",
            "item_purchased",
        }
        if event_type and event_type not in accepted_types:
            log.debug(
                "Webhook: ignoring event_type=%r for creator %s", event_type, creator_id
            )
            return web.json_response(
                {"ok": True, "ignored": True, "event_type": event_type}
            )

        event_id: str | None = fields["event_id"]

        # 10. Compute fallback hash if event_id is missing.
        fallback_event_hash: str | None = None
        if not event_id:
            fallback_event_hash = _compute_fallback_hash(
                creator_id=creator_id,
                order_id=fields["order_id"],
                purchased_at=fields["purchased_at"],
                gifter_username=fields["gifter_username"],
                item_name=fields["item_name"],
                amount_cents=fields["amount_cents"],
                currency=fields["currency"],
            )

        domme_user_id = int(matched_row.discord_user_id)
        amount_usd: float | None = fields["amount_usd"]
        is_private = fields["is_private"]

        if event_type == "gift_purchased":
            amount_usd = None
            is_private = True
            cached_items = await self.database.get_throne_wishlist_items(
                creator_id=creator_id
            )
            cached_price = match_wishlist_item_price(
                [
                    WishlistItemRecord(
                        wishlist_item_id=item.wishlist_item_id,
                        item_name=item.item_name,
                        item_image_url=item.item_image_url,
                        amount_usd=item.amount_usd,
                        currency=item.currency,
                        is_available=item.is_available,
                    )
                    for item in cached_items
                ],
                item_name=fields["item_name"],
                item_image_url=fields["item_image_url"],
            )
            if cached_price is not None:
                amount_usd = cached_price.amount_usd
                is_private = False
            else:
                live_items = await fetch_public_wishlist_items(
                    creator_id,
                    http=await self._get_http(),
                    timeout_seconds=self.config.throne_http_timeout_seconds,
                )
                if live_items is not None:
                    now_str = _utc_now()
                    await self.database.replace_throne_wishlist_items(
                        creator_id=creator_id,
                        items=[
                            ThroneWishlistItem(
                                creator_id=creator_id,
                                wishlist_item_id=item.wishlist_item_id,
                                item_name=item.item_name,
                                item_image_url=item.item_image_url,
                                amount_usd=item.amount_usd,
                                currency=item.currency,
                                is_available=item.is_available,
                                last_seen_at=now_str,
                            )
                            for item in live_items
                        ],
                    )
                    live_price = match_wishlist_item_price(
                        live_items,
                        item_name=fields["item_name"],
                        item_image_url=fields["item_image_url"],
                    )
                    if live_price is not None:
                        amount_usd = live_price.amount_usd
                        is_private = False
                if amount_usd is None:
                    log.info(
                        "Webhook gift_purchased for creator %s could not be enriched with a cached or live public wishlist price.",
                        creator_id,
                    )

        if amount_usd is None:
            amount_usd = 0.0

        # Determine active event key.
        event_cog = self.bot.get_cog("RobEventCog")
        event_key: str | None = None
        if event_cog is not None:
            ctx = await event_cog.get_runtime_context()
            if ctx is not None and ctx.is_event_active:
                event_key = ctx.event_key

        tracker_cog = self.bot.get_cog("ThroneTrackerCog")
        if tracker_cog is None:
            log.warning("Webhook received send but ThroneTrackerCog is unavailable.")
            return web.Response(status=503, text="Tracker unavailable")

        # 11. Insert + notify + leaderboard refresh.
        result = await tracker_cog.record_send(
            domme_user_id=domme_user_id,
            sub_name=normalize_sender_name(fields["gifter_username"]),
            amount_usd=amount_usd,
            item_name=fields["item_name"],
            item_image_url=fields["item_image_url"],
            source="webhook",
            is_private=is_private,
            sent_at=fields["purchased_at"],
            event_key=event_key,
            external_id=None,
            event_id=event_id or None,
            fallback_event_hash=fallback_event_hash,
            seeded=False,
        )

        if result is None:
            # Duplicate.
            return web.json_response({"ok": True, "duplicate": True})
        send_id, send_public_id = result

        # 12. Update throne_creators tracking state.
        now_str = _utc_now()
        await self.database.update_throne_creator_webhook_connected(
            creator_id=matched_row.id,
            webhook_connected_at=now_str,
            last_successful_event_at=now_str,
        )

        # 13. Return success.
        return web.json_response(
            {
                "ok": True,
                "inserted": True,
                "send_id": send_id,
                "send_public_id": send_public_id,
            }
        )

    # ── Admin endpoints ─────────────────────────────────────────────────────

    @staticmethod
    def _is_local_request(request: web.Request) -> bool:
        candidate_hosts: list[str] = []
        if request.remote:
            candidate_hosts.append(request.remote)
        transport = request.transport
        if transport is not None:
            peer = transport.get_extra_info("peername")
            if isinstance(peer, tuple) and peer:
                candidate_hosts.append(str(peer[0]))

        for host in candidate_hosts:
            normalized = host.strip().strip("[]")
            if not normalized:
                continue
            if normalized.casefold() == "localhost":
                return True
            if normalized.casefold().startswith("::ffff:127."):
                return True
            try:
                if ipaddress.ip_address(normalized).is_loopback:
                    return True
            except ValueError:
                continue
        return False

    async def _resolve_owner(self) -> discord.User | discord.Member | None:
        app_info = await self.bot.application_info()
        owner = app_info.owner
        if isinstance(owner, (discord.User, discord.Member)):
            return owner
        return None

    async def _broadcast_view_to_users(
        self,
        *,
        user_ids: set[int],
        view_factory: Callable[[], BroadcastNotificationView] | None = None,
        plain_content: str | None = None,
    ) -> tuple[int, list[int]]:
        delivered = 0
        failed: list[int] = []
        for user_id in sorted(user_ids):
            user = self.bot.get_user(user_id)
            if user is None:
                try:
                    user = await self.bot.fetch_user(user_id)
                except (discord.NotFound, discord.HTTPException):
                    failed.append(user_id)
                    continue
            try:
                if plain_content is not None:
                    await user.send(content=plain_content)
                else:
                    await user.send(view=view_factory())  # type: ignore[misc]
            except discord.HTTPException:
                failed.append(user_id)
                continue
            delivered += 1
        return delivered, failed

    async def _handle_admin_maintenance(self, request: web.Request) -> web.Response:
        if not self._is_local_request(request):
            return web.json_response({"ok": False, "error": "forbidden"}, status=403)
        try:
            payload = await request.json()
        except (json.JSONDecodeError, ValueError):
            return web.json_response({"ok": False, "error": "invalid json"}, status=400)
        if not isinstance(payload, dict) or "active" not in payload:
            return web.json_response(
                {"ok": False, "error": "missing 'active' boolean"}, status=400
            )
        active = bool(payload.get("active"))
        self.bot.maintenance_mode = active
        log.warning("Maintenance mode set to %s via admin endpoint.", active)

        event_cog = self.bot.get_cog("RobEventCog")
        if event_cog is not None:
            try:
                await event_cog.sync_leaderboard_channel()
            except Exception:  # noqa: BLE001 - never let UI sync block the response
                log.exception("Failed to sync leaderboard after maintenance toggle.")

        return web.json_response({"ok": True, "maintenance_mode": active})

    async def _handle_admin_leaderboard_sync(
        self, request: web.Request
    ) -> web.Response:
        if not self._is_local_request(request):
            return web.json_response({"ok": False, "error": "forbidden"}, status=403)

        event_cog = self.bot.get_cog("RobEventCog")
        if event_cog is None:
            return web.json_response(
                {"ok": False, "error": "event cog unavailable"}, status=503
            )

        try:
            await event_cog.sync_leaderboard_channel()
        except Exception:  # noqa: BLE001 - keep endpoint resilient and log details
            log.exception("Failed to sync leaderboard via admin endpoint.")
            return web.json_response({"ok": False, "error": "sync failed"}, status=500)

        return web.json_response({"ok": True, "synced": True})

    async def _handle_admin_broadcast(self, request: web.Request) -> web.Response:
        if not self._is_local_request(request):
            return web.json_response({"ok": False, "error": "forbidden"}, status=403)
        try:
            payload = await request.json()
        except (json.JSONDecodeError, ValueError):
            return web.json_response({"ok": False, "error": "invalid json"}, status=400)
        if not isinstance(payload, dict):
            return web.json_response(
                {"ok": False, "error": "invalid payload"}, status=400
            )

        target = str(payload.get("target") or "").strip().lower()
        dm_user_id: int | None = None
        channel_id: int | None = None
        if target.startswith("user:"):
            raw_uid = target[len("user:") :]
            if not raw_uid.isdigit():
                return web.json_response(
                    {
                        "ok": False,
                        "error": "user target must be 'user:<discord_user_id>' with a numeric ID",
                    },
                    status=400,
                )
            dm_user_id = int(raw_uid)
        elif target.startswith("channel:"):
            raw_channel_id = target[len("channel:") :]
            if not raw_channel_id.isdigit():
                return web.json_response(
                    {
                        "ok": False,
                        "error": "channel target must be 'channel:<discord_channel_id>' with a numeric ID",
                    },
                    status=400,
                )
            channel_id = int(raw_channel_id)
        elif target not in {"owner", "all", "dommes", "subs"}:
            return web.json_response(
                {
                    "ok": False,
                    "error": "target must be one of: owner, all, dommes, subs, user:<discord_user_id>, channel:<discord_channel_id>",
                },
                status=400,
            )

        message = str(payload.get("message") or "").strip()
        if not message:
            return web.json_response(
                {"ok": False, "error": "missing 'message'"}, status=400
            )
        raw_url = payload.get("url")
        action_url = str(raw_url).strip() if raw_url else None
        if action_url and not (
            action_url.startswith("http://") or action_url.startswith("https://")
        ):
            return web.json_response(
                {"ok": False, "error": "url must be http(s)"}, status=400
            )
        plain = bool(payload.get("plain", False))
        if plain and action_url:
            return web.json_response(
                {"ok": False, "error": "url cannot be used with plain mode"}, status=400
            )

        def _view_factory() -> BroadcastNotificationView:
            return BroadcastNotificationView(
                title="📣 Rob | Broadcast",
                message=message,
                action_url=action_url or None,
            )

        async def _send(user: discord.User | discord.Member) -> None:
            if plain:
                await user.send(content=message)
            else:
                await user.send(view=_view_factory())

        if target == "owner":
            owner = await self._resolve_owner()
            if owner is None:
                return web.json_response(
                    {"ok": False, "error": "no owner resolvable"}, status=500
                )
            try:
                await _send(owner)
            except discord.HTTPException:
                log.exception("Failed to deliver owner broadcast notification.")
                return web.json_response(
                    {"ok": False, "error": "discord delivery failed"}, status=502
                )
            return web.json_response(
                {
                    "ok": True,
                    "target": "owner",
                    "delivered_count": 1,
                    "failed_user_ids": [],
                }
            )

        if dm_user_id is not None:
            user = self.bot.get_user(dm_user_id)
            if user is None:
                try:
                    user = await self.bot.fetch_user(dm_user_id)
                except discord.NotFound:
                    return web.json_response(
                        {"ok": False, "error": f"user {dm_user_id} not found"},
                        status=404,
                    )
                except discord.HTTPException:
                    log.exception(
                        "Failed to fetch user %s for direct broadcast.", dm_user_id
                    )
                    return web.json_response(
                        {"ok": False, "error": "discord lookup failed"}, status=502
                    )
            try:
                await _send(user)
            except discord.HTTPException:
                log.exception(
                    "Failed to deliver direct broadcast to user %s.", dm_user_id
                )
                return web.json_response(
                    {"ok": False, "error": "discord delivery failed"}, status=502
                )
            return web.json_response(
                {
                    "ok": True,
                    "target": target,
                    "delivered_count": 1,
                    "failed_user_ids": [],
                }
            )

        if channel_id is not None:
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                try:
                    channel = await self.bot.fetch_channel(channel_id)
                except discord.NotFound:
                    return web.json_response(
                        {"ok": False, "error": f"channel {channel_id} not found"},
                        status=404,
                    )
                except discord.Forbidden:
                    return web.json_response(
                        {
                            "ok": False,
                            "error": f"missing access to channel {channel_id}",
                        },
                        status=403,
                    )
                except discord.HTTPException:
                    log.exception(
                        "Failed to fetch channel %s for direct broadcast.", channel_id
                    )
                    return web.json_response(
                        {"ok": False, "error": "discord lookup failed"}, status=502
                    )
            if not hasattr(channel, "send"):
                return web.json_response(
                    {
                        "ok": False,
                        "error": f"channel {channel_id} does not support sending messages",
                    },
                    status=400,
                )
            try:
                if plain:
                    await channel.send(content=message)  # type: ignore[attr-defined]
                else:
                    await channel.send(view=_view_factory())  # type: ignore[attr-defined]
            except discord.Forbidden:
                return web.json_response(
                    {
                        "ok": False,
                        "error": f"missing permission to send to channel {channel_id}",
                    },
                    status=403,
                )
            except discord.HTTPException:
                log.exception(
                    "Failed to deliver direct broadcast to channel %s.", channel_id
                )
                return web.json_response(
                    {"ok": False, "error": "discord delivery failed"}, status=502
                )
            return web.json_response(
                {
                    "ok": True,
                    "target": target,
                    "delivered_count": 1,
                    "failed_user_ids": [],
                }
            )

        user_ids: set[int] = set()
        if target in {"all", "dommes"}:
            dommes = await self.database.get_all_event_dommes()
            user_ids.update(row.user_id for row in dommes)
        if target in {"all", "subs"}:
            subs = await self.database.get_all_event_subs()
            user_ids.update(row.user_id for row in subs)

        delivered_count, failed_user_ids = await self._broadcast_view_to_users(
            user_ids=user_ids,
            view_factory=_view_factory if not plain else None,
            plain_content=message if plain else None,
        )

        return web.json_response(
            {
                "ok": True,
                "target": target,
                "candidate_count": len(user_ids),
                "delivered_count": delivered_count,
                "failed_count": len(failed_user_ids),
                "failed_user_ids": failed_user_ids,
            }
        )
