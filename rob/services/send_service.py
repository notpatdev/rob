from __future__ import annotations

import inspect
import logging

from rob.database.repositories.leaderboards import LeaderboardsRepository
from rob.database.repositories.models import Domme, NewSend, SendRecord
from rob.database.repositories.sends import SendsRepository
from rob.database.repositories.subs import SubsRepository
from rob.services.maintenance_service import MaintenanceService
from rob.services.throne_service import ThroneService
from rob.throne.payloads import ThroneSendPayload, is_known_test_sender
from rob.utils.fx import UnsupportedCurrencyError, convert_cents_to_usd
from rob.utils.text import parse_user_mention
from rob.utils.time import utc_now

log = logging.getLogger(__name__)

_UNMATCHABLE_SUB_NAMES = {"anonymous", "anon", "private", "hidden"}


def _safe_convert_cents_to_usd(amount_cents: int, currency: str | None) -> int | None:
    """Convert to USD cents, or None for an unknown/invalid currency.

    Throne can send arbitrary currency strings; returning None instead of
    raising lets the webhook handler record the event so Throne stops retrying.
    """
    try:
        return convert_cents_to_usd(amount_cents, currency)
    except (UnsupportedCurrencyError, ValueError):
        return None


def _can_match_sub_name(name: str | None, *, is_private: bool = False) -> bool:
    if is_private:
        return False
    if not name:
        return False
    return name.strip().casefold() not in _UNMATCHABLE_SUB_NAMES


class SendService:
    def __init__(
        self,
        *,
        sends: SendsRepository,
        subs: SubsRepository,
        maintenance: MaintenanceService,
        leaderboards: LeaderboardsRepository | None = None,
        throne: ThroneService | None = None,
        throne_test_gifter_usernames: tuple[str, ...] = (),
        include_test_sends: bool = False,
        test_gifter_usernames: tuple[str, ...] = (),
        owner_test_user_id: int | None = None,
    ) -> None:
        self.sends = sends
        self.subs = subs
        self.maintenance = maintenance
        self.leaderboards = leaderboards
        self.throne = throne
        self.throne_test_gifter_usernames = throne_test_gifter_usernames
        self.include_test_sends = include_test_sends
        self.test_gifter_usernames = test_gifter_usernames
        self.owner_test_user_id = owner_test_user_id

    async def _send_tracking_disabled_for_guild(self, guild_id: int | None) -> bool:
        checker = getattr(self.maintenance, "send_tracking_disabled_for_guild", None)
        if checker is None:
            return False
        result = checker(guild_id)
        if inspect.isawaitable(result):
            return bool(await result)
        if isinstance(result, bool):
            return result
        return False

    async def record_throne_send(
        self,
        *,
        creator: Domme,
        payload: ThroneSendPayload,
    ) -> SendRecord | None:
        source_amount_cents = payload.amount_cents
        source_currency = (payload.currency or "USD").upper()
        converted = _safe_convert_cents_to_usd(source_amount_cents, source_currency)
        conversion_failed = converted is None
        amount_cents = converted or 0
        currency = "USD"
        original_amount_cents = source_amount_cents if source_currency != "USD" else None
        original_currency = source_currency if source_currency != "USD" else None
        is_private = payload.is_private
        is_test_send = is_known_test_sender(
            payload.gifter_username,
            test_gifter_usernames=set(self.throne_test_gifter_usernames),
        )

        if conversion_failed:
            log.warning(
                "Throne send currency could not be converted to USD; recording as "
                "ignored. guild_id=%s creator_id=%s currency=%s amount_cents=%s",
                creator.guild_id,
                creator.throne_creator_id,
                source_currency,
                source_amount_cents,
            )

        if (
            not conversion_failed
            and amount_cents == 0
            and payload.event_type == "gift_purchased"
            and self.throne is not None
        ):
            match = await self.throne.match_item(
                creator_id=creator.throne_creator_id,
                item_name=payload.item_name,
                item_image_url=payload.item_image_url,
            )
            if match is not None and match.amount_cents > 0:
                matched_currency = (match.currency or "USD").upper()
                matched_usd = _safe_convert_cents_to_usd(match.amount_cents, matched_currency)
                if matched_usd is not None:
                    amount_cents = matched_usd
                    currency = "USD"
                    original_amount_cents = (
                        match.amount_cents if matched_currency != "USD" else None
                    )
                    original_currency = matched_currency if matched_currency != "USD" else None
                    is_private = False

        if conversion_failed:
            # Stored with original amount/currency but never posted or counted,
            # so an unknown currency can't corrupt USD totals.
            status = "ignored"
        elif await self._send_tracking_disabled_for_guild(creator.guild_id):
            status = "posted"
        else:
            status = "queued_maintenance" if await self.maintenance.is_enabled() else "pending"

        sub_id = None
        sub_user_id = None
        if _can_match_sub_name(payload.gifter_username, is_private=payload.is_private):
            sub = await self.subs.get_by_send_name(creator.guild_id, payload.gifter_username)
            if sub is not None:
                sub_id = sub.id
                sub_user_id = sub.discord_user_id

        return await self.sends.insert(
            NewSend(
                guild_id=creator.guild_id,
                domme_id=creator.id,
                domme_user_id=creator.discord_user_id,
                sub_id=sub_id,
                sub_user_id=sub_user_id,
                sub_name=payload.gifter_username,
                amount_cents=amount_cents,
                currency=currency,
                original_amount_cents=original_amount_cents,
                original_currency=original_currency,
                method="throne",
                source="throne_webhook",
                item_name=payload.item_name,
                item_image_url=payload.item_image_url,
                external_id=None,
                event_id=payload.event_id,
                fallback_event_hash=payload.fallback_event_hash,
                is_private=is_private,
                seeded=False,
                sent_at=payload.purchased_at,
                discord_post_status=status,
                is_test_send=is_test_send,
            )
        )

    async def record_manual_send(
        self,
        *,
        guild_id: int,
        domme_id: int | None,
        domme_user_id: int,
        amount_cents: int,
        currency: str,
        method: str,
        note: str | None,
        sub_name: str | None = None,
        sub_user_id: int | None = None,
        sub_id: int | None = None,
        source: str | None = None,
    ) -> SendRecord | None:
        if await self._send_tracking_disabled_for_guild(guild_id):
            status = "posted"
        else:
            status = "queued_maintenance" if await self.maintenance.is_enabled() else "pending"
        resolved_sub_id = sub_id
        resolved_sub_user_id = sub_user_id
        resolved_sub_name = sub_name

        # A sub name that is really a Discord mention ("<@123>") identifies a
        # user, not a nickname; attribute the send to that user.
        if resolved_sub_user_id is None:
            mentioned_user_id = parse_user_mention(resolved_sub_name)
            if mentioned_user_id is not None:
                resolved_sub_user_id = mentioned_user_id
                resolved_sub_name = None

        if resolved_sub_user_id is not None:
            sub = await self.subs.get_by_user_id(guild_id, resolved_sub_user_id)
            if sub is not None:
                resolved_sub_id = sub.id
                if not resolved_sub_name:
                    resolved_sub_name = sub.send_name
        elif _can_match_sub_name(resolved_sub_name, is_private=False):
            sub = await self.subs.get_by_send_name(guild_id, resolved_sub_name)
            if sub is not None:
                resolved_sub_id = sub.id
                resolved_sub_user_id = sub.discord_user_id

        return await self.sends.insert(
            NewSend(
                guild_id=guild_id,
                domme_id=domme_id,
                domme_user_id=domme_user_id,
                sub_id=resolved_sub_id,
                sub_user_id=resolved_sub_user_id,
                sub_name=resolved_sub_name,
                amount_cents=amount_cents,
                currency=currency,
                method=method,
                source=source or f"manual:{method}",
                item_name=note or f"Manual send via {method}",
                item_image_url=None,
                external_id=None,
                event_id=None,
                fallback_event_hash=None,
                is_private=False,
                seeded=False,
                sent_at=utc_now(),
                discord_post_status=status,
                is_test_send=False,
            )
        )
