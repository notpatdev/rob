from __future__ import annotations

import logging
import time

import discord

from rob.database.repositories.dommes import DommesRepository
from rob.models import Domme, SendChangeRequest, SendRecord
from rob.database.repositories.send_change_requests import SendChangeRequestsRepository
from rob.database.repositories.sends import SendsRepository
from rob.services.leaderboard_service import LeaderboardService
from rob.services.send_queue_service import SendQueueService
from rob.services.send_service import SendService
from rob.ui.cards.errors import error_card
from rob.ui.cards.send_change_requests import (
    send_change_request_card,
    send_change_result_card,
)
from rob.ui.render import add_card_actions
from rob.utils.money import format_money_from_cents, format_money_with_currency_name

log = logging.getLogger(__name__)


def _normalize_lookup(value: str) -> str:
    cleaned = value.strip()
    if cleaned.startswith("@"):
        cleaned = cleaned[1:]
    return cleaned


def _send_amount_text(send: SendRecord) -> str:
    currency = (send.currency or "USD").upper()
    amount = format_money_from_cents(send.amount_cents, currency)
    original_currency = (send.original_currency or "").upper()
    if currency == "USD" and send.original_amount_cents is not None and original_currency and original_currency != "USD":
        original = format_money_with_currency_name(send.original_amount_cents, original_currency)
        return f"{amount} (converted from {original})"
    if currency != "USD":
        return format_money_with_currency_name(send.amount_cents, currency)
    return amount


class _SendChangeDecisionButton(discord.ui.Button):
    def __init__(
        self,
        *,
        service: SendChangeRequestService,
        request_id: int,
        approve: bool,
    ) -> None:
        action = "approve" if approve else "reject"
        super().__init__(
            label="Approve" if approve else "Reject",
            style=discord.ButtonStyle.success if approve else discord.ButtonStyle.danger,
            custom_id=f"send-change:{action}:{request_id}",
        )
        self.service = service
        self.request_id = request_id
        self.approve = approve

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user is None:
            await interaction.response.send_message(
                **error_card("Rob could not identify the approver.").send_kwargs(),
                ephemeral=True,
            )
            return

        try:
            rendered = (
                await self.service.approve_request(
                    request_id=self.request_id,
                    approved_by_user_id=interaction.user.id,
                )
                if self.approve
                else await self.service.reject_request(
                    request_id=self.request_id,
                    approved_by_user_id=interaction.user.id,
                )
            )
        except PermissionError:
            await interaction.response.send_message(
                **error_card("This approval flow belongs to someone else.").send_kwargs(),
                ephemeral=True,
            )
            return
        except ValueError as exc:
            await interaction.response.send_message(
                **error_card("This approval request is no longer pending.", str(exc)).send_kwargs(),
                ephemeral=True,
            )
            return
        except Exception:
            log.exception("Send change approval callback failed request_id=%s", self.request_id)
            await interaction.response.send_message(
                **error_card(
                    "Rob could not finish that backend send change.",
                    "The request is still recorded in the backend logs for review.",
                ).send_kwargs(),
                ephemeral=True,
            )
            return

        await interaction.response.edit_message(**rendered.edit_kwargs())


class SendChangeRequestService:
    def __init__(
        self,
        *,
        bot: discord.Client,
        requests: SendChangeRequestsRepository,
        dommes: DommesRepository,
        sends: SendsRepository,
        send_service: SendService,
        send_queue_service: SendQueueService | None,
        leaderboard_service: LeaderboardService | None,
    ) -> None:
        self.bot = bot
        self.requests = requests
        self.dommes = dommes
        self.sends = sends
        self.send_service = send_service
        self.send_queue_service = send_queue_service
        self.leaderboard_service = leaderboard_service

    async def rebind_pending_views(self) -> None:
        for request in await self.requests.list_pending():
            if request.request_message_id is None:
                continue
            self.bot.add_view(
                self._build_view(request.id),
                message_id=request.request_message_id,
            )

    async def create_send_add_request(
        self,
        *,
        guild_id: int,
        domme_lookup: str,
        amount_cents: int,
        sub_name: str | None,
        requested_by: str,
        currency: str = "USD",
        method: str = "manual",
        note: str | None = None,
    ) -> SendChangeRequest:
        domme = await self._resolve_domme(guild_id, domme_lookup)
        if domme is None:
            raise ValueError("That Dom/me lookup did not match a registered profile.")

        request = await self.requests.create_send_add_request(
            guild_id=guild_id,
            domme_user_id=domme.discord_user_id,
            requested_by=requested_by,
            amount_cents=amount_cents,
            currency=currency,
            method=method,
            note=note,
            sub_name=sub_name,
        )
        return await self._deliver_request(request, domme=domme, target_send=None)

    async def create_send_remove_request(
        self,
        *,
        guild_id: int,
        domme_lookup: str,
        send_id: int,
        requested_by: str,
    ) -> SendChangeRequest:
        domme = await self._resolve_domme(guild_id, domme_lookup)
        if domme is None:
            raise ValueError("That Dom/me lookup did not match a registered profile.")

        send = await self.sends.get(send_id)
        if send is None or send.guild_id != guild_id:
            raise ValueError("That send ID was not found for this guild.")
        if send.domme_user_id != domme.discord_user_id:
            raise ValueError("That send does not belong to the selected Dom/me.")

        request = await self.requests.create_send_remove_request(
            guild_id=guild_id,
            domme_user_id=domme.discord_user_id,
            requested_by=requested_by,
            target_send_id=send_id,
        )
        return await self._deliver_request(request, domme=domme, target_send=send)

    async def create_send_update_request(
        self,
        *,
        guild_id: int,
        domme_lookup: str,
        send_id: int,
        amount_cents: int,
        message_id: int,
        reason: str,
        requested_by: str,
        currency: str = "USD",
    ) -> SendChangeRequest:
        domme = await self._resolve_domme(guild_id, domme_lookup)
        if domme is None:
            raise ValueError("That Dom/me lookup did not match a registered profile.")

        send = await self.sends.get(send_id)
        if send is None or send.guild_id != guild_id:
            raise ValueError("That send ID was not found for this guild.")
        if send.domme_user_id != domme.discord_user_id:
            raise ValueError("That send does not belong to the selected Dom/me.")
        if send.discord_message_id is None:
            raise ValueError("That send is missing a posted Discord message reference.")
        if send.discord_message_id != message_id:
            raise ValueError("That message ID does not match the target send announcement.")

        request = await self.requests.create_send_update_request(
            guild_id=guild_id,
            domme_user_id=domme.discord_user_id,
            requested_by=requested_by,
            target_send_id=send_id,
            amount_cents=amount_cents,
            currency=currency,
            note=reason,
        )
        return await self._deliver_request(request, domme=domme, target_send=send)

    async def approve_request(
        self,
        *,
        request_id: int,
        approved_by_user_id: int,
    ):
        request = await self.requests.get(request_id)
        if request is None:
            raise ValueError("That request no longer exists.")
        if request.domme_user_id != approved_by_user_id:
            raise PermissionError("Only the target Dom/me can approve this request.")
        if request.status != "pending":
            raise ValueError(f"Request is already {request.status}.")

        if request.action == "send_add":
            domme = await self.dommes.get_by_user_id(request.guild_id, request.domme_user_id)
            if domme is None:
                await self.requests.mark_failed(
                    request_id=request_id,
                    approved_by_user_id=approved_by_user_id,
                    decision_reason="Dom/me profile was no longer registered during approval.",
                )
                raise ValueError("The Dom/me profile was no longer registered.")

            send = await self.send_service.record_manual_send(
                guild_id=request.guild_id,
                domme_id=domme.id,
                domme_user_id=domme.discord_user_id,
                amount_cents=request.amount_cents or 0,
                currency=request.currency or "USD",
                method=request.method or "manual",
                note=request.note,
                sub_name=request.requested_sub_name,
                source="manual:approved_backend_request",
            )
            if send is None:
                await self.requests.mark_failed(
                    request_id=request_id,
                    approved_by_user_id=approved_by_user_id,
                    decision_reason="Rob could not record the approved send.",
                )
                raise ValueError("Rob could not record the approved send.")

            approved = await self.requests.mark_approved(
                request_id=request_id,
                approved_by_user_id=approved_by_user_id,
                approved_send_id=send.id,
            )
            if approved is None:
                raise ValueError("This request was already processed.")

            if self.send_queue_service is not None:
                await self.send_queue_service.notify_send(send.id)

            return send_change_result_card(
                title="Rob | Send Added",
                summary="Rob recorded the approved backend send change.",
                details=[
                    ("Send ID", str(send.id)),
                    ("Amount", format_money_from_cents(send.amount_cents)),
                    ("Sender", send.sub_name or "Unclaimed"),
                    ("Status", send.discord_post_status),
                ],
                approved=True,
            )

        target_send = await self.sends.get(request.target_send_id or 0)
        if target_send is None:
            await self.requests.mark_failed(
                request_id=request_id,
                approved_by_user_id=approved_by_user_id,
                decision_reason="Target send was no longer available during approval.",
            )
            raise ValueError("The target send was no longer available.")

        if request.action == "send_update":
            old_amount = _send_amount_text(target_send)
            updated_send = await self.sends.update_amount(
                target_send.id,
                amount_cents=request.amount_cents or 0,
                currency=request.currency or "USD",
            )
            if updated_send is None:
                await self.requests.mark_failed(
                    request_id=request_id,
                    approved_by_user_id=approved_by_user_id,
                    decision_reason="Target send amount could not be updated.",
                )
                raise ValueError("Rob could not update that send amount.")

            unix_timestamp = int(time.time())
            reason = request.note or "No reason provided."
            adjustment_note = (
                f"-# NOTE: This send has been adjusted by {request.requested_by} "
                f"on {unix_timestamp} | Reason: {reason}"
            )
            approved = await self.requests.mark_approved(
                request_id=request_id,
                approved_by_user_id=approved_by_user_id,
                approved_send_id=updated_send.id,
                decision_reason=adjustment_note,
            )
            if approved is None:
                raise ValueError("This request was already processed.")

            message_refresh_status = "not requested"
            if target_send.discord_message_id is not None and self.send_queue_service is not None:
                refreshed = await self.send_queue_service.refresh_send_message(
                    send_id=updated_send.id,
                    message_id=target_send.discord_message_id,
                    adjustment_note=adjustment_note,
                )
                message_refresh_status = "updated in place" if refreshed else "update failed"
            elif target_send.discord_message_id is None:
                message_refresh_status = "missing message reference"

            if self.leaderboard_service is not None:
                await self.leaderboard_service.refresh_guild(request.guild_id)

            return send_change_result_card(
                title="Rob | Send Updated",
                summary="Rob updated the approved send amount and refreshed tracked totals.",
                details=[
                    ("Send ID", str(updated_send.id)),
                    ("Previous Amount", old_amount),
                    ("Updated Amount", format_money_from_cents(updated_send.amount_cents, "USD")),
                    ("Message", message_refresh_status),
                ],
                approved=True,
            )

        updated = await self.sends.mark_ignored(
            target_send.id,
            reason="Approved backend removal request.",
        )
        if updated <= 0:
            await self.requests.mark_failed(
                request_id=request_id,
                approved_by_user_id=approved_by_user_id,
                decision_reason="Target send could not be marked ignored.",
            )
            raise ValueError("Rob could not remove that send from tracked totals.")

        approved = await self.requests.mark_approved(
            request_id=request_id,
            approved_by_user_id=approved_by_user_id,
            approved_send_id=target_send.id,
        )
        if approved is None:
            raise ValueError("This request was already processed.")

        if self.leaderboard_service is not None:
            await self.leaderboard_service.refresh_guild(request.guild_id)

        return send_change_result_card(
            title="Rob | Send Removed",
            summary="Rob removed the approved send from tracked totals.",
            details=[
                ("Send ID", str(target_send.id)),
                ("Amount", format_money_from_cents(target_send.amount_cents)),
                ("Sender", target_send.sub_name or "Unclaimed"),
                ("Status", "ignored"),
            ],
            approved=True,
        )

    async def reject_request(
        self,
        *,
        request_id: int,
        approved_by_user_id: int,
    ):
        request = await self.requests.get(request_id)
        if request is None:
            raise ValueError("That request no longer exists.")
        if request.domme_user_id != approved_by_user_id:
            raise PermissionError("Only the target Dom/me can reject this request.")
        rejected = await self.requests.mark_rejected(
            request_id=request_id,
            approved_by_user_id=approved_by_user_id,
        )
        if rejected is None:
            raise ValueError(f"Request is already {request.status}.")
        return send_change_result_card(
            title="Rob | Send Change Rejected",
            summary="Rob did not apply that backend send change.",
            details=[
                ("Request ID", str(request.id)),
                ("Action", request.action.replace("_", " ")),
                ("Requested By", request.requested_by),
            ],
            approved=False,
        )

    def _build_view(self, request_id: int) -> discord.ui.LayoutView:
        view = discord.ui.LayoutView(timeout=None)
        add_card_actions(
            view,
            _SendChangeDecisionButton(service=self, request_id=request_id, approve=True),
            _SendChangeDecisionButton(service=self, request_id=request_id, approve=False),
        )
        return view

    def _build_action_buttons(self, request_id: int) -> tuple[discord.ui.Button, discord.ui.Button]:
        return (
            _SendChangeDecisionButton(service=self, request_id=request_id, approve=True),
            _SendChangeDecisionButton(service=self, request_id=request_id, approve=False),
        )

    async def _deliver_request(
        self,
        request: SendChangeRequest,
        *,
        domme: Domme,
        target_send: SendRecord | None,
    ) -> SendChangeRequest:
        user = self.bot.get_user(domme.discord_user_id)
        if user is None:
            try:
                user = await self.bot.fetch_user(domme.discord_user_id)
            except discord.HTTPException as exc:
                await self.requests.mark_failed(
                    request_id=request.id,
                    approved_by_user_id=None,
                    decision_reason="Rob could not fetch the target Dom/me for approval.",
                )
                raise ValueError("Rob could not fetch the target Dom/me for approval.") from exc

        domme_label = (
            domme.public_display_name
            or (f"@{domme.throne_handle}" if domme.throne_handle else f"<@{domme.discord_user_id}>")
        )
        view = discord.ui.LayoutView(timeout=None)
        rendered = send_change_request_card(
            request,
            domme_label=domme_label,
            target_send=target_send,
            view=view,
        )
        add_card_actions(view, *self._build_action_buttons(request.id))
        try:
            message = await user.send(**rendered.send_kwargs())
        except discord.Forbidden as exc:
            await self.requests.mark_failed(
                request_id=request.id,
                approved_by_user_id=None,
                decision_reason="Could not DM the target Dom/me for approval.",
            )
            raise ValueError("Rob could not DM the target Dom/me for approval.") from exc
        except discord.HTTPException as exc:
            await self.requests.mark_failed(
                request_id=request.id,
                approved_by_user_id=None,
                decision_reason="Rob could not deliver the approval message to the target Dom/me.",
            )
            raise ValueError("Rob could not deliver the approval message to the target Dom/me.") from exc

        updated = await self.requests.set_delivery(
            request_id=request.id,
            request_channel_id=message.channel.id,
            request_message_id=message.id,
        )
        self.bot.add_view(view, message_id=message.id)
        log.info(
            "Delivered send change request id=%s action=%s guild_id=%s domme_user_id=%s",
            updated.id,
            updated.action,
            updated.guild_id,
            updated.domme_user_id,
        )
        return updated

    async def _resolve_domme(self, guild_id: int, lookup: str) -> Domme | None:
        normalized = _normalize_lookup(lookup)
        if normalized.isdigit():
            return await self.dommes.get_by_user_id(guild_id, int(normalized))

        direct = await self.dommes.get_by_handle(guild_id, normalized)
        if direct is not None:
            return direct

        lowered = normalized.casefold()
        for domme in await self.dommes.list_for_guild(guild_id):
            if (domme.public_display_name or "").casefold() == lowered:
                return domme
        return None
