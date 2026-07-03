"""DM-based Throne onboarding flow for the test guild.

Owns the state machine only; the cog sends/edits the DMs, ThroneService
resolves creators, and the repositories persist state.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from rob.config.guilds import is_new_system_guild
from rob.database.repositories.domme_onboarding import (
    STAGE_AWAITING_PREFERENCES,
    STAGE_AWAITING_THRONE_INPUT,
    STAGE_AWAITING_WEBHOOK_TEST,
    STAGE_CONFIRMING_IDENTITY,
    DommeOnboardingRepository,
)
from rob.database.repositories.dommes import DommesRepository
from rob.services.registration_service import RegistrationService
from rob.services.throne_service import ThroneService
from rob.throne.scraper import normalize_throne_registration_input


class OnboardingError(Exception):
    """Raised when the onboarding flow cannot proceed."""


@dataclass(frozen=True)
class ResolvedThroneIdentity:
    throne_handle: str
    throne_display_name: str | None
    creator_id: str
    normalized_input: str


class DMOnboardingService:
    def __init__(
        self,
        *,
        onboarding: DommeOnboardingRepository,
        dommes: DommesRepository,
        throne: ThroneService,
        registration: RegistrationService,
    ) -> None:
        self.onboarding = onboarding
        self.dommes = dommes
        self.throne = throne
        self.registration = registration

    # -- gating ------------------------------------------------------------

    @staticmethod
    def is_enabled_for(guild_id: int | None) -> bool:
        return is_new_system_guild(guild_id)

    # -- stage 1: intro -> modal submission --------------------------------

    async def start(self, *, guild_id: int, discord_user_id: int) -> None:
        if not self.is_enabled_for(guild_id):
            raise OnboardingError("DM onboarding is only available in the test guild.")
        await self.onboarding.start(
            guild_id=guild_id,
            discord_user_id=discord_user_id,
        )
        await self.onboarding.set_stage(
            guild_id=guild_id,
            discord_user_id=discord_user_id,
            stage=STAGE_AWAITING_THRONE_INPUT,
        )

    async def submit_throne_input(
        self,
        *,
        guild_id: int,
        discord_user_id: int,
        throne_input: str,
    ) -> ResolvedThroneIdentity:
        if not self.is_enabled_for(guild_id):
            raise OnboardingError("DM onboarding is only available in the test guild.")
        normalized = normalize_throne_registration_input(throne_input)
        if normalized is None:
            raise OnboardingError("That Throne link or username could not be understood.")

        creator_info = await self.throne.resolve_creator(normalized)
        if creator_info is None:
            raise OnboardingError("Rob could not resolve that Throne creator right now.")

        identity = ResolvedThroneIdentity(
            throne_handle=creator_info.throne_handle,
            throne_display_name=getattr(creator_info, "display_name", None),
            creator_id=creator_info.creator_id,
            normalized_input=normalized,
        )
        await self.onboarding.set_pending_throne(
            guild_id=guild_id,
            discord_user_id=discord_user_id,
            throne_handle=identity.throne_handle,
            throne_creator_id=identity.creator_id,
            throne_input=identity.normalized_input,
        )
        await self.onboarding.set_stage(
            guild_id=guild_id,
            discord_user_id=discord_user_id,
            stage=STAGE_CONFIRMING_IDENTITY,
        )
        return identity

    # -- stage 2: confirm identity -> register + webhook -------------------

    async def confirm_identity(
        self,
        *,
        guild_id: int,
        discord_user_id: int,
    ) -> str | None:
        """Register the Dom/me and return their webhook URL, or None if no
        webhook base URL is configured."""
        if not self.is_enabled_for(guild_id):
            raise OnboardingError("DM onboarding is only available in the test guild.")
        state = await self.onboarding.get(guild_id=guild_id, discord_user_id=discord_user_id)
        if state is None or not state.pending_throne_input:
            raise OnboardingError("Throne input is missing; please restart onboarding.")

        result = await self.registration.register_domme(
            guild_id=guild_id,
            discord_user_id=discord_user_id,
            throne_input=state.pending_throne_input,
        )
        await self.onboarding.set_stage(
            guild_id=guild_id,
            discord_user_id=discord_user_id,
            stage=STAGE_AWAITING_WEBHOOK_TEST,
        )
        return result.webhook_url

    async def reject_identity(self, *, guild_id: int, discord_user_id: int) -> None:
        """Rewind to the Throne-input stage after the user rejects the match."""
        if not self.is_enabled_for(guild_id):
            raise OnboardingError("DM onboarding is only available in the test guild.")
        # There is no clear-pending API; leave the row and just rewind the stage.
        await self.onboarding.set_stage(
            guild_id=guild_id,
            discord_user_id=discord_user_id,
            stage=STAGE_AWAITING_THRONE_INPUT,
        )

    # -- stage 3: webhook test detected ------------------------------------

    async def mark_webhook_received(
        self,
        *,
        guild_id: int,
        discord_user_id: int,
    ) -> None:
        if not self.is_enabled_for(guild_id):
            raise OnboardingError("DM onboarding is only available in the test guild.")
        await self.onboarding.set_stage(
            guild_id=guild_id,
            discord_user_id=discord_user_id,
            stage=STAGE_AWAITING_PREFERENCES,
        )

    # -- stage 4: preference save + completion -----------------------------

    async def save_preferences(
        self,
        *,
        guild_id: int,
        discord_user_id: int,
    ) -> None:
        if not self.is_enabled_for(guild_id):
            raise OnboardingError("DM onboarding is only available in the test guild.")
        domme = await self.dommes.get_by_user_id(guild_id, discord_user_id)
        if domme is None:
            raise OnboardingError("You are not registered yet.")
        await self.dommes.set_preferences(
            guild_id=guild_id,
            discord_user_id=discord_user_id,
            confirm=True,
        )
        await self.onboarding.complete(
            guild_id=guild_id,
            discord_user_id=discord_user_id,
        )

    # -- migration prompt --------------------------------------------------

    async def defer_migration(
        self,
        *,
        guild_id: int,
        discord_user_id: int,
        days: int = 7,
    ) -> None:
        if not self.is_enabled_for(guild_id):
            raise OnboardingError("DM onboarding is only available in the test guild.")
        domme = await self.dommes.get_by_user_id(guild_id, discord_user_id)
        if domme is None:
            raise OnboardingError("You are not registered yet.")
        until = datetime.now(timezone.utc) + timedelta(days=days)
        await self.dommes.defer_preferences(
            guild_id=guild_id,
            discord_user_id=discord_user_id,
            until=until,
        )
