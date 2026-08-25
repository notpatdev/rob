from __future__ import annotations

import logging
from typing import Any

import discord
from aiohttp import web

from rob.utils.money import dollars_to_cents

log = logging.getLogger(__name__)


class SendRoutes:

    async def _handle_refresh_public_names(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)

        guild_id = self._match_guild_id(request)
        if guild_id is None:
            return web.json_response({"error": "invalid_guild_id"}, status=400)

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return web.json_response({"error": "guild_not_in_cache", "guild_id": guild_id}, status=404)

        if not hasattr(self.bot, "dommes_repo"):
            return web.json_response({"error": "dommes_repo_unavailable"}, status=500)

        dommes = await self.bot.dommes_repo.list_for_guild(guild_id)
        updated = 0
        for domme in dommes:
            label: str | None = None
            member = guild.get_member(domme.discord_user_id)
            if member is None:
                try:
                    member = await guild.fetch_member(domme.discord_user_id)
                except (discord.NotFound, discord.HTTPException):
                    member = None
            if member is not None:
                label = (member.display_name or member.name or "").strip() or None

            if label:
                await self.bot.dommes_repo.set_public_display_name(
                    guild_id=guild_id,
                    discord_user_id=domme.discord_user_id,
                    label=label,
                )
                updated += 1

        return web.json_response(
            {
                "ok": True,
                "guild_id": guild_id,
                "registered_dommes": len(dommes),
                "updated_display_names": updated,
            }
        )

    async def _handle_refresh_leaderboard(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)

        guild_id = self._match_guild_id(request)
        if guild_id is None:
            return web.json_response({"error": "invalid_guild_id"}, status=400)

        if not hasattr(self.bot, "leaderboard_service"):
            return web.json_response({"error": "leaderboard_service_unavailable"}, status=500)

        refreshed = await self.bot.leaderboard_service.refresh_guild(guild_id)
        return web.json_response({"ok": bool(refreshed), "guild_id": guild_id, "refreshed": bool(refreshed)})

    async def _handle_process_send(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)

        if not hasattr(self.bot, "send_queue_service"):
            return web.json_response({"error": "send_queue_service_unavailable"}, status=500)

        payload = await self._json_payload(request)

        try:
            send_id = int(payload.get("send_id"))
        except (TypeError, ValueError):
            return web.json_response({"error": "invalid_send_id"}, status=400)

        guild_id = payload.get("guild_id")
        try:
            guild_id = int(guild_id) if guild_id is not None else None
        except (TypeError, ValueError):
            return web.json_response({"error": "invalid_guild_id"}, status=400)

        await self.bot.send_queue_service.notify_send(send_id)
        log.info(
            "Accepted send processing notification send_id=%s guild_id=%s.",
            send_id,
            guild_id,
        )
        return web.json_response(
            {
                "ok": True,
                "queued": True,
                "send_id": send_id,
                "guild_id": guild_id,
            }
        )

    async def _handle_onboarding_webhook_verified(
        self, request: web.Request
    ) -> web.Response:
        """Advance an in-progress DM onboarding flow when the Throne test
        webhook arrives. Test-guild-only; the cog also enforces the gate."""

        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)

        payload = await self._json_payload(request)
        try:
            guild_id = int(payload.get("guild_id"))
            discord_user_id = int(payload.get("discord_user_id"))
        except (TypeError, ValueError):
            log.warning(
                "Onboarding webhook auto-advance invalid payload received: %r",
                payload,
            )
            return web.json_response({"error": "invalid_payload"}, status=400)

        log.info(
            "Onboarding webhook auto-advance request received guild_id=%s "
            "discord_user_id=%s",
            guild_id,
            discord_user_id,
        )

        cog = self.bot.get_cog("DMOnboardingCog") if hasattr(self.bot, "get_cog") else None
        if cog is None:
            log.warning(
                "Onboarding webhook auto-advance cog unavailable guild_id=%s "
                "discord_user_id=%s",
                guild_id,
                discord_user_id,
            )
            return web.json_response(
                {"error": "dm_onboarding_cog_unavailable"}, status=500
            )
        try:
            advanced = await cog.on_throne_test_webhook_received(
                guild_id=guild_id,
                discord_user_id=discord_user_id,
            )
        except Exception:
            log.exception(
                "Auto-advance onboarding DM failed guild_id=%s user_id=%s",
                guild_id,
                discord_user_id,
            )
            return web.json_response({"error": "auto_advance_failed"}, status=500)
        log.info(
            "Onboarding webhook auto-advance guild_id=%s user_id=%s advanced=%s",
            guild_id,
            discord_user_id,
            advanced,
        )
        return web.json_response(
            {
                "ok": True,
                "advanced": bool(advanced),
                "guild_id": guild_id,
                "discord_user_id": discord_user_id,
            }
        )

    async def _handle_request_send_add(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)
        if not hasattr(self.bot, "send_change_request_service"):
            return web.json_response({"error": "send_change_request_service_unavailable"}, status=500)
        guild_id = self._match_guild_id(request)
        if guild_id is None:
            return web.json_response({"error": "invalid_guild_id"}, status=400)
        payload = await self._json_payload(request)
        domme_lookup = str(payload.get("domme_lookup") or "").strip()
        requested_by = str(payload.get("requested_by") or "rob-cli").strip() or "rob-cli"
        sub_name = str(payload.get("sub_name") or "").strip() or None
        note = str(payload.get("note") or "").strip() or None
        method = str(payload.get("method") or "manual").strip() or "manual"
        currency = str(payload.get("currency") or "USD").strip().upper() or "USD"
        if not domme_lookup:
            return web.json_response({"error": "missing_domme_lookup"}, status=400)
        try:
            amount = float(payload.get("amount"))
        except (TypeError, ValueError):
            return web.json_response({"error": "invalid_amount"}, status=400)
        if amount <= 0:
            return web.json_response({"error": "invalid_amount"}, status=400)
        try:
            change_request = await self.bot.send_change_request_service.create_send_add_request(
                guild_id=guild_id,
                domme_lookup=domme_lookup,
                amount_cents=dollars_to_cents(amount),
                sub_name=sub_name,
                requested_by=requested_by,
                currency=currency,
                method=method,
                note=note,
            )
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except Exception:
            log.exception("Send add approval request failed guild_id=%s domme_lookup=%s", guild_id, domme_lookup)
            return web.json_response(
                {"error": "Rob could not create the send approval request just now."},
                status=500,
            )
        payload = {
            "ok": True,
            "request_id": change_request.id,
            "action": change_request.action,
            "status": change_request.status,
            "domme_user_id": change_request.domme_user_id,
        }
        if self._wants_text(request):
            return web.Response(
                text=self._format_send_request_text(payload),
                content_type="text/plain",
            )
        return web.json_response(payload)

    async def _handle_request_send_remove(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)
        if not hasattr(self.bot, "send_change_request_service"):
            return web.json_response({"error": "send_change_request_service_unavailable"}, status=500)
        guild_id = self._match_guild_id(request)
        if guild_id is None:
            return web.json_response({"error": "invalid_guild_id"}, status=400)
        payload = await self._json_payload(request)
        domme_lookup = str(payload.get("domme_lookup") or "").strip()
        requested_by = str(payload.get("requested_by") or "rob-cli").strip() or "rob-cli"
        if not domme_lookup:
            return web.json_response({"error": "missing_domme_lookup"}, status=400)
        try:
            send_id = int(payload.get("send_id"))
        except (TypeError, ValueError):
            return web.json_response({"error": "invalid_send_id"}, status=400)
        try:
            change_request = await self.bot.send_change_request_service.create_send_remove_request(
                guild_id=guild_id,
                domme_lookup=domme_lookup,
                send_id=send_id,
                requested_by=requested_by,
            )
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except Exception:
            log.exception(
                "Send remove approval request failed guild_id=%s domme_lookup=%s send_id=%s",
                guild_id,
                domme_lookup,
                send_id,
            )
            return web.json_response(
                {"error": "Rob could not create the send removal approval request just now."},
                status=500,
            )
        payload = {
            "ok": True,
            "request_id": change_request.id,
            "action": change_request.action,
            "status": change_request.status,
            "domme_user_id": change_request.domme_user_id,
            "target_send_id": change_request.target_send_id,
        }
        if self._wants_text(request):
            return web.Response(
                text=self._format_send_request_text(payload),
                content_type="text/plain",
            )
        return web.json_response(payload)

    async def _handle_request_send_update(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)
        if not hasattr(self.bot, "send_change_request_service"):
            return web.json_response({"error": "send_change_request_service_unavailable"}, status=500)
        guild_id = self._match_guild_id(request)
        if guild_id is None:
            return web.json_response({"error": "invalid_guild_id"}, status=400)
        payload = await self._json_payload(request)
        domme_lookup = str(payload.get("domme_lookup") or "").strip()
        requested_by = str(payload.get("requested_by") or "rob-cli").strip() or "rob-cli"
        reason = str(payload.get("reason") or "").strip()
        if not domme_lookup:
            return web.json_response({"error": "missing_domme_lookup"}, status=400)
        if not reason:
            return web.json_response({"error": "missing_reason"}, status=400)
        try:
            send_id = int(payload.get("send_id"))
            message_id = int(payload.get("message_id"))
            amount = float(payload.get("amount"))
        except (TypeError, ValueError):
            return web.json_response({"error": "invalid_update_payload"}, status=400)
        if amount <= 0:
            return web.json_response({"error": "invalid_amount"}, status=400)
        try:
            change_request = await self.bot.send_change_request_service.create_send_update_request(
                guild_id=guild_id,
                domme_lookup=domme_lookup,
                send_id=send_id,
                amount_cents=dollars_to_cents(amount),
                message_id=message_id,
                reason=reason,
                requested_by=requested_by,
                # rob send update amount input is an operator-provided USD override.
                currency="USD",
            )
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except Exception:
            log.exception(
                "Send update approval request failed guild_id=%s domme_lookup=%s send_id=%s",
                guild_id,
                domme_lookup,
                send_id,
            )
            return web.json_response(
                {"error": "Rob could not create the send update approval request just now."},
                status=500,
            )
        payload = {
            "ok": True,
            "request_id": change_request.id,
            "action": change_request.action,
            "status": change_request.status,
            "domme_user_id": change_request.domme_user_id,
            "target_send_id": change_request.target_send_id,
        }
        if self._wants_text(request):
            return web.Response(
                text=self._format_send_request_text(payload),
                content_type="text/plain",
            )
        return web.json_response(payload)

    @staticmethod
    def _format_send_request_text(payload: dict[str, Any]) -> str:
        lines = [
            "Send Approval Requested",
            f"Request ID: {payload['request_id']}",
            f"Action: {payload['action']}",
            f"Status: {payload['status']}",
            f"Dom/me User ID: {payload['domme_user_id']}",
        ]
        if payload.get("target_send_id") is not None:
            lines.append(f"Target Send ID: {payload['target_send_id']}")
        lines.append("Next Step: the target Dom/me must approve this change in Discord.")
        return "\n".join(lines)
