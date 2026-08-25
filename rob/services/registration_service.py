from __future__ import annotations

import secrets
from dataclasses import dataclass

from rob.database.repositories.blacklist import BlacklistRepository
from rob.database.repositories.dommes import DommesRepository
from rob.models import Domme, Sub
from rob.database.repositories.vib_settings import VibSettingsRepository
from rob.database.repositories.subs import SubsRepository
from rob.services.throne_service import ThroneService
from rob.throne.scraper import normalize_throne_registration_input
from rob.throne.security import hash_webhook_secret
from rob.utils.text import collapse_whitespace

_RESERVED_SUB_NAMES = {"anonymous", "anon", "private", "hidden"}


def sanitize_webhook_base_url(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip().strip("\"'")
    while cleaned.startswith("="):
        cleaned = cleaned[1:].lstrip()
    cleaned = cleaned.rstrip("/")
    return cleaned or None


@dataclass(frozen=True)
class DommeRegistrationResult:
    domme: Domme
    webhook_url: str | None


@dataclass(frozen=True)
class SubRegistrationResult:
    sub: Sub
    send_names: tuple[str, ...]


class RegistrationService:
    def __init__(
        self,
        *,
        guild_settings: VibSettingsRepository,
        dommes: DommesRepository,
        subs: SubsRepository,
        blacklist: BlacklistRepository,
        throne: ThroneService,
        webhook_base_url: str | None = None,
    ) -> None:
        self.guild_settings = guild_settings
        self.dommes = dommes
        self.subs = subs
        self.blacklist = blacklist
        self.throne = throne
        self.webhook_base_url = sanitize_webhook_base_url(webhook_base_url)

    def build_webhook_url(self, *, creator_id: str, webhook_secret: str) -> str | None:
        if not self.webhook_base_url:
            return None
        return f"{self.webhook_base_url}/webhook/{creator_id}/{webhook_secret}"

    async def register_domme(
        self,
        *,
        guild_id: int,
        discord_user_id: int,
        throne_input: str,
    ) -> DommeRegistrationResult:
        if await self.blacklist.contains(discord_user_id):
            raise ValueError("You are currently blocked from registering.")

        normalized = normalize_throne_registration_input(throne_input)
        if normalized is None:
            raise ValueError("That Throne link or username could not be understood.")

        creator_info = await self.throne.resolve_creator(normalized)
        if creator_info is None:
            raise ValueError("Rob could not resolve that Throne creator right now.")

        await self.guild_settings.ensure_guild(guild_id)

        existing_by_handle = await self.dommes.get_by_handle(
            guild_id,
            creator_info.throne_handle,
        )
        if existing_by_handle is not None and existing_by_handle.discord_user_id != discord_user_id:
            raise ValueError("That Throne account is already linked to another Dom/me.")
        existing_by_creator_id = await self.dommes.get_by_creator_id(
            creator_info.creator_id
        )
        for existing_creator in existing_by_creator_id:
            if (
                existing_creator.guild_id == guild_id
                and existing_creator.discord_user_id != discord_user_id
            ):
                raise ValueError("That Throne creator is already linked to another Dom/me.")

        existing_for_user = await self.dommes.get_by_user_id(guild_id, discord_user_id)
        webhook_secret = (
            existing_for_user.webhook_secret
            if existing_for_user is not None and existing_for_user.webhook_secret
            else secrets.token_urlsafe(32)
        )

        domme = await self.dommes.upsert(
            guild_id=guild_id,
            discord_user_id=discord_user_id,
            throne_url=normalized,
            throne_handle=creator_info.throne_handle,
            throne_creator_id=creator_info.creator_id,
            hide_own_purchases=creator_info.hide_own_purchases,
            tracking_status="disabled",
            profile_status="active",
            webhook_secret=webhook_secret,
            webhook_secret_hash=hash_webhook_secret(webhook_secret),
        )

        webhook_url = self.build_webhook_url(
            creator_id=creator_info.creator_id,
            webhook_secret=webhook_secret,
        )

        return DommeRegistrationResult(
            domme=domme,
            webhook_url=webhook_url,
        )

    async def reissue_domme_webhook(
        self,
        *,
        guild_id: int,
        discord_user_id: int,
    ) -> DommeRegistrationResult:
        domme = await self.dommes.get_by_user_id(guild_id, discord_user_id)
        if domme is None:
            raise ValueError("That Dom/me is no longer registered.")

        throne_handle = domme.throne_handle
        throne_creator_id = domme.throne_creator_id
        throne_url = domme.throne_url
        hide_own_purchases = domme.hide_own_purchases

        if not throne_creator_id or not throne_handle:
            lookup = throne_url or throne_handle
            if not lookup:
                raise ValueError("Rob could not rebuild the webhook URL because no Throne profile is saved.")
            creator_info = await self.throne.resolve_creator(lookup)
            if creator_info is None:
                raise ValueError("Rob could not resolve the saved Throne profile just now.")
            throne_handle = creator_info.throne_handle
            throne_creator_id = creator_info.creator_id
            hide_own_purchases = creator_info.hide_own_purchases
            if throne_url is None:
                throne_url = lookup

        webhook_secret = secrets.token_urlsafe(32)
        domme = await self.dommes.rotate_webhook_secret(
            guild_id=guild_id,
            discord_user_id=discord_user_id,
            throne_url=throne_url,
            throne_handle=throne_handle,
            throne_creator_id=throne_creator_id,
            hide_own_purchases=hide_own_purchases,
            webhook_secret=webhook_secret,
            webhook_secret_hash=hash_webhook_secret(webhook_secret),
        )
        webhook_url = self.build_webhook_url(
            creator_id=throne_creator_id,
            webhook_secret=webhook_secret,
        )
        return DommeRegistrationResult(domme=domme, webhook_url=webhook_url)

    async def register_sub(
        self,
        *,
        guild_id: int,
        discord_user_id: int,
        send_name: str | None = None,
        send_names: list[str] | None = None,
    ) -> SubRegistrationResult:
        if await self.blacklist.contains(discord_user_id):
            raise ValueError("You are currently blocked from registering.")

        input_names = send_names if send_names is not None else ([send_name] if send_name is not None else [])
        cleaned_names = [collapse_whitespace(name.strip()) for name in input_names if name and name.strip()]
        if not cleaned_names:
            raise ValueError("At least one sending name is required.")
        if len(cleaned_names) > 3:
            raise ValueError("You can register up to 3 sending names.")

        deduped: list[str] = []
        seen: set[str] = set()
        for name in cleaned_names:
            lowered = name.casefold()
            if lowered in _RESERVED_SUB_NAMES:
                raise ValueError(f"'{name}' is reserved and cannot be claimed.")
            if lowered in seen:
                raise ValueError("Duplicate sending names are not allowed.")
            seen.add(lowered)
            deduped.append(name)

        await self.guild_settings.ensure_guild(guild_id)

        sub = await self.subs.upsert_with_send_names(
            guild_id=guild_id,
            discord_user_id=discord_user_id,
            send_names=deduped,
        )
        return SubRegistrationResult(sub=sub, send_names=tuple(deduped))
