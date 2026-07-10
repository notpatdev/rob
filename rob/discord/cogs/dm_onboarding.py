"""DM-based Dom/me onboarding flow: buttons, modals, and staged DM edits.

Buttons are built with cog=self so the live LayoutView dispatches real
callbacks; custom-id-only buttons inside a LayoutView are silent no-ops.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
import logging

import discord
from discord.ext import commands

from rob.config.guilds import is_new_system_guild
from rob.discord.leaderboard_access import apply_leaderboard_access
from rob.services.dm_onboarding_service import (
    DMOnboardingService,
    OnboardingError,
)
from rob.ui.cards.dm_onboarding import (
    ID_MIGRATION_LEADERBOARD_ACCESS,
    ID_PREFS_LEADERBOARD_ACCESS,
    IdentityNoButton,
    IdentityYesButton,
    LEADERBOARD_ACCESS_ON_VALUE,
    MigrationDeferButton,
    MigrationOpenPrefsButton,
    MigrationPromptView,
    MigrationSaveButton,
    OpenModalButton,
    PreferencesView,
    SavePrefsButton,
    WebhookRetryButton,
    build_intro_modal,
    identity_confirm_card,
    intro_card,
    migration_prompt_card,
    onboarding_error_card,
    preferences_card,
    success_card,
    webhook_setup_card,
)

if TYPE_CHECKING:
    from rob.discord.client import RobBot


log = logging.getLogger(__name__)

_RERUN_REGISTER_DOMME_MESSAGE = (
    "I lost your server context for this setup. Please run /register domme "
    "again in the server to re-establish it."
)


class _PersistentInteractionsView(discord.ui.View):
    """Fallback view registered at startup. discord.py's ViewStore routes
    interactions here (matched on custom_id alone) when the live LayoutView
    is no longer in memory, typically after a bot restart.
    """

    def __init__(self, cog: "DMOnboardingCog") -> None:
        super().__init__(timeout=None)
        self.add_item(OpenModalButton(cog))
        self.add_item(IdentityYesButton(cog))
        self.add_item(IdentityNoButton(cog))
        self.add_item(WebhookRetryButton(cog))
        self.add_item(SavePrefsButton(cog))
        self.add_item(MigrationSaveButton(cog))
        self.add_item(MigrationDeferButton(cog))
        self.add_item(MigrationOpenPrefsButton(cog))


class DMOnboardingCog(commands.Cog):
    """Runtime cog for the DM-based onboarding flow (test guild only)."""

    def __init__(self, bot: "RobBot") -> None:
        self.bot = bot

    @property
    def service(self) -> DMOnboardingService | None:
        return getattr(self.bot, "dm_onboarding_service", None)

    def register_persistent_views(self) -> None:
        """Register the fallback view so button custom IDs survive restarts."""

        try:
            self.bot.add_view(_PersistentInteractionsView(self))
        except Exception:
            log.warning(
                "Failed to register persistent DM onboarding view; "
                "post-restart interactions may not route.",
                exc_info=True,
            )
            return
        log.info("Registered persistent DM onboarding view")

    async def start_onboarding_dm(
        self,
        *,
        user: discord.abc.User,
        guild_id: int,
    ) -> tuple[bool, discord.Message | None, str | None]:
        """Start onboarding for user. Returns (ok, message, error_text);
        the caller handles the ephemeral slash response."""

        service = self.service
        if service is None or not is_new_system_guild(guild_id):
            return False, None, "DM onboarding is not available here."

        log.info(
            "DM onboarding start user_id=%s guild_id=%s", user.id, guild_id
        )
        try:
            await service.start(guild_id=guild_id, discord_user_id=user.id)
        except OnboardingError as exc:
            log.warning(
                "DM onboarding start refused user_id=%s guild_id=%s: %s",
                user.id,
                guild_id,
                exc,
            )
            return False, None, str(exc)

        rendered = intro_card(
            name=getattr(user, "display_name", None) or user.name,
            cog=self,
        )
        try:
            message = await user.send(**rendered.send_kwargs())
        except discord.Forbidden:
            log.warning(
                "DM onboarding intro could not be sent (forbidden) user_id=%s guild_id=%s",
                user.id,
                guild_id,
            )
            return False, None, "Rob couldn’t DM you. Please allow DMs from this server and try again."
        except discord.HTTPException as exc:
            log.exception(
                "DM onboarding intro send failed user_id=%s guild_id=%s: %s",
                user.id,
                guild_id,
                exc,
            )
            return False, None, "Rob couldn’t send the setup DM right now."

        await self._persist_dm_message(
            guild_id=guild_id,
            discord_user_id=user.id,
            message=message,
        )
        return True, message, None

    async def send_migration_prompt(
        self,
        *,
        user: discord.abc.User,
        guild_id: int,
    ) -> discord.Message | None:
        """DM the migration prompt to an already-registered Dom/me. Returns
        the message, or None on failure."""

        if not is_new_system_guild(guild_id):
            return None
        rendered = migration_prompt_card(
            name=getattr(user, "display_name", None) or user.name,
            cog=self,
        )
        try:
            return await user.send(**rendered.send_kwargs())
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.warning(
                "Migration prompt DM failed user_id=%s guild_id=%s: %s",
                user.id,
                guild_id,
                exc,
            )
            return None

    async def _persist_dm_message(
        self,
        *,
        guild_id: int,
        discord_user_id: int,
        message: discord.Message,
    ) -> None:
        repo = getattr(self.bot, "domme_onboarding_repo", None)
        if repo is None:
            return
        try:
            await repo.set_dm_message(
                guild_id=guild_id,
                discord_user_id=discord_user_id,
                dm_channel_id=int(message.channel.id),
                dm_message_id=int(message.id),
            )
        except Exception:
            log.exception(
                "Failed to persist DM onboarding message ids user_id=%s guild_id=%s",
                discord_user_id,
                guild_id,
            )

    async def _resolve_guild_id_for_user(self, user_id: int) -> int | None:
        """Fallback guild lookup for older DM flows that still depend on the
        historic test-guild onboarding row."""

        repo = getattr(self.bot, "domme_onboarding_repo", None)
        if repo is None:
            return None
        from rob.config.guilds import TEST_GUILD_ID

        try:
            state = await repo.get(
                guild_id=TEST_GUILD_ID,
                discord_user_id=user_id,
            )
        except Exception:
            log.exception("Onboarding state lookup failed user_id=%s", user_id)
            return None
        if state is None:
            return None
        return int(state.guild_id)

    @staticmethod
    def _extract_interaction_custom_id(
        interaction: discord.Interaction,
    ) -> str | None:
        data = getattr(interaction, "data", None)
        if isinstance(data, dict):
            return data.get("custom_id")
        if data is None:
            return None
        return getattr(data, "custom_id", None)

    async def _resolve_dm_onboarding_guild_id(
        self, interaction: discord.Interaction
    ) -> int | None:
        user_id = interaction.user.id
        custom_id = self._extract_interaction_custom_id(interaction)
        repo = getattr(self.bot, "domme_onboarding_repo", None)
        state_found = False

        if repo is not None:
            try:
                state = await repo.get_latest_for_user(discord_user_id=user_id)
            except Exception as exc:
                log.exception(
                    "DM onboarding guild resolution state lookup failed "
                    "user_id=%s custom_id=%s interaction_guild_id=%s "
                    "exc_type=%s",
                    user_id,
                    custom_id,
                    getattr(interaction, "guild_id", None),
                    type(exc).__name__,
                )
            else:
                state_found = state is not None
                if state is not None and state.guild_id is not None:
                    guild_id = int(state.guild_id)
                    log.info(
                        "DM onboarding guild resolved user_id=%s custom_id=%s "
                        "state_found=%s fallback_resolved=%s guild_id=%s source=state",
                        user_id,
                        custom_id,
                        state_found,
                        False,
                        guild_id,
                    )
                    return guild_id

        fallback_guild_id = await self._resolve_guild_id_for_user(user_id)
        fallback_resolved = fallback_guild_id is not None
        if fallback_guild_id is not None:
            log.info(
                "DM onboarding guild resolved user_id=%s custom_id=%s "
                "state_found=%s fallback_resolved=%s guild_id=%s source=fallback",
                user_id,
                custom_id,
                state_found,
                fallback_resolved,
                fallback_guild_id,
            )
            return fallback_guild_id

        log.warning(
            "DM onboarding guild resolution failed user_id=%s custom_id=%s "
            "state_found=%s fallback_resolved=%s guild_id=%s reason=%s",
            user_id,
            custom_id,
            state_found,
            fallback_resolved,
            None,
            "missing_onboarding_state_and_fallback",
        )
        return None

    async def _edit_stored_dm(
        self,
        *,
        guild_id: int,
        discord_user_id: int,
        rendered: Any,
    ) -> bool:
        repo = getattr(self.bot, "domme_onboarding_repo", None)
        if repo is None:
            return False
        try:
            state = await repo.get(
                guild_id=guild_id,
                discord_user_id=discord_user_id,
            )
        except Exception:
            log.exception(
                "Onboarding state lookup failed during edit user_id=%s guild_id=%s",
                discord_user_id,
                guild_id,
            )
            return False
        if state is None or state.dm_channel_id is None or state.dm_message_id is None:
            log.warning(
                "No stored DM message for onboarding user_id=%s guild_id=%s",
                discord_user_id,
                guild_id,
            )
            return False

        try:
            user = self.bot.get_user(discord_user_id) or await self.bot.fetch_user(
                discord_user_id
            )
            dm_channel = user.dm_channel or await user.create_dm()
            message = dm_channel.get_partial_message(int(state.dm_message_id))
            await message.edit(**rendered.edit_kwargs())
            return True
        except discord.NotFound:
            log.warning(
                "Stored onboarding DM is gone user_id=%s guild_id=%s message_id=%s",
                discord_user_id,
                guild_id,
                state.dm_message_id,
            )
            return False
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.warning(
                "Could not edit stored onboarding DM user_id=%s guild_id=%s: %s",
                discord_user_id,
                guild_id,
                exc,
            )
            return False

    async def handle_open_modal(self, interaction: discord.Interaction) -> None:
        log.info(
            "handle_open_modal user_id=%s guild_id=%s channel_id=%s",
            interaction.user.id,
            interaction.guild_id,
            getattr(interaction, "channel_id", None),
        )
        guild_id = interaction.guild_id or await self._resolve_dm_onboarding_guild_id(
            interaction
        )
        if guild_id is None:
            log.warning(
                "Onboarding open_modal rejected due to missing guild context "
                "user_id=%s custom_id=%s",
                interaction.user.id,
                self._extract_interaction_custom_id(interaction),
            )
            await interaction.response.send_message(
                _RERUN_REGISTER_DOMME_MESSAGE, ephemeral=True
            )
            return
        if not is_new_system_guild(guild_id):
            log.warning(
                "Onboarding open_modal rejected (no guild or wrong guild) "
                "user_id=%s guild_id=%s",
                interaction.user.id,
                guild_id,
            )
            await interaction.response.send_message(
                "This setup isn't available right now.", ephemeral=True
            )
            return
        try:
            await interaction.response.send_modal(
                build_intro_modal(cog=self, guild_id=int(guild_id))
            )
        except discord.HTTPException:
            log.exception(
                "Failed to open Throne input modal user_id=%s guild_id=%s",
                interaction.user.id,
                guild_id,
            )

    async def handle_modal_submit(
        self,
        interaction: discord.Interaction,
        *,
        guild_id: int,
        throne_input: str,
    ) -> None:
        log.info(
            "handle_modal_submit user_id=%s guild_id=%s",
            interaction.user.id,
            guild_id,
        )
        service = self.service
        if service is None or not is_new_system_guild(guild_id):
            await interaction.response.send_message(
                "This setup isn't available right now.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)
        try:
            identity = await service.submit_throne_input(
                guild_id=guild_id,
                discord_user_id=interaction.user.id,
                throne_input=throne_input,
            )
        except OnboardingError as exc:
            log.warning(
                "Throne resolution failed user_id=%s guild_id=%s: %s",
                interaction.user.id,
                guild_id,
                exc,
            )
            rendered = onboarding_error_card(str(exc), cog=self)
            edited = await self._edit_stored_dm(
                guild_id=guild_id,
                discord_user_id=interaction.user.id,
                rendered=rendered,
            )
            if not edited:
                await interaction.followup.send(str(exc), ephemeral=True)
            else:
                await interaction.followup.send(
                    "Couldn’t resolve that — check your DM and try again.",
                    ephemeral=True,
                )
            return

        rendered = identity_confirm_card(
            throne_handle=identity.throne_handle,
            throne_display_name=identity.throne_display_name,
            cog=self,
        )
        ok = await self._edit_stored_dm(
            guild_id=guild_id,
            discord_user_id=interaction.user.id,
            rendered=rendered,
        )
        if not ok:
            try:
                message = await interaction.user.send(**rendered.send_kwargs())
                await self._persist_dm_message(
                    guild_id=guild_id,
                    discord_user_id=interaction.user.id,
                    message=message,
                )
            except (discord.Forbidden, discord.HTTPException):
                log.exception(
                    "Fallback DM send after modal submit failed "
                    "user_id=%s guild_id=%s",
                    interaction.user.id,
                    guild_id,
                )
                await interaction.followup.send(
                    "Rob couldn’t update your DM. Please re-run /register domme.",
                    ephemeral=True,
                )
                return
        await interaction.followup.send(
            "Got it — check your DMs to confirm.", ephemeral=True
        )

    async def handle_identity_yes(self, interaction: discord.Interaction) -> None:
        log.info(
            "handle_identity_yes user_id=%s", interaction.user.id
        )
        guild_id = await self._resolve_dm_onboarding_guild_id(interaction)
        service = self.service
        if guild_id is None:
            await interaction.response.send_message(
                _RERUN_REGISTER_DOMME_MESSAGE, ephemeral=True
            )
            return
        if service is None or not is_new_system_guild(guild_id):
            await interaction.response.send_message(
                "This setup isn't available right now.", ephemeral=True
            )
            return

        await interaction.response.defer()
        try:
            webhook_url = await service.confirm_identity(
                guild_id=guild_id,
                discord_user_id=interaction.user.id,
            )
        except OnboardingError as exc:
            log.warning(
                "confirm_identity failed user_id=%s guild_id=%s: %s",
                interaction.user.id,
                guild_id,
                exc,
            )
            await interaction.followup.send(str(exc), ephemeral=True)
            return
        except ValueError as exc:
            log.warning(
                "confirm_identity ValueError user_id=%s guild_id=%s: %s",
                interaction.user.id,
                guild_id,
                exc,
            )
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        if not webhook_url:
            log.error(
                "No webhook URL generated user_id=%s guild_id=%s",
                interaction.user.id,
                guild_id,
            )
            await interaction.followup.send(
                "Rob couldn’t generate your webhook URL. Ask staff to verify "
                "THRONE_WEBHOOK_BASE_URL on the bot server.",
                ephemeral=True,
            )
            return

        rendered = webhook_setup_card(webhook_url=webhook_url, cog=self)
        await self._edit_or_resend(
            interaction=interaction,
            guild_id=guild_id,
            rendered=rendered,
        )

    async def handle_identity_no(self, interaction: discord.Interaction) -> None:
        log.info("handle_identity_no user_id=%s", interaction.user.id)
        guild_id = await self._resolve_dm_onboarding_guild_id(interaction)
        service = self.service
        if guild_id is None:
            await interaction.response.send_message(
                _RERUN_REGISTER_DOMME_MESSAGE, ephemeral=True
            )
            return
        if service is None or not is_new_system_guild(guild_id):
            await interaction.response.send_message(
                "This setup isn't available right now.", ephemeral=True
            )
            return

        await interaction.response.defer()
        try:
            await service.reject_identity(
                guild_id=guild_id,
                discord_user_id=interaction.user.id,
            )
        except OnboardingError as exc:
            log.warning(
                "reject_identity failed user_id=%s guild_id=%s: %s",
                interaction.user.id,
                guild_id,
                exc,
            )
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        name = getattr(interaction.user, "display_name", None) or interaction.user.name
        rendered = intro_card(name=name, cog=self)
        await self._edit_or_resend(
            interaction=interaction,
            guild_id=guild_id,
            rendered=rendered,
        )

    async def handle_webhook_retry(self, interaction: discord.Interaction) -> None:
        """Rotate the webhook URL so a leaked or stale one is invalidated,
        then re-render the waiting card with the new URL."""

        log.info("handle_webhook_retry user_id=%s", interaction.user.id)
        guild_id = await self._resolve_dm_onboarding_guild_id(interaction)
        if guild_id is None:
            await interaction.response.send_message(
                _RERUN_REGISTER_DOMME_MESSAGE, ephemeral=True
            )
            return
        if not is_new_system_guild(guild_id):
            await interaction.response.send_message(
                "This setup isn't available right now.", ephemeral=True
            )
            return

        await interaction.response.defer()
        webhook_url: str | None = None
        registration_service = getattr(self.bot, "registration_service", None)
        if registration_service is not None:
            try:
                result = await registration_service.reissue_domme_webhook(
                    guild_id=guild_id,
                    discord_user_id=interaction.user.id,
                )
                webhook_url = result.webhook_url
            except Exception:
                log.exception(
                    "Webhook reissue failed during onboarding retry "
                    "user_id=%s guild_id=%s",
                    interaction.user.id,
                    guild_id,
                )

        if not webhook_url:
            dommes = getattr(self.bot, "dommes_repo", None)
            if dommes is not None and registration_service is not None:
                try:
                    domme = await dommes.get_by_user_id(
                        guild_id, interaction.user.id
                    )
                    if (
                        domme is not None
                        and domme.webhook_secret
                        and domme.throne_creator_id
                    ):
                        webhook_url = registration_service.build_webhook_url(
                            creator_id=domme.throne_creator_id,
                            webhook_secret=domme.webhook_secret,
                        )
                except Exception:
                    log.exception(
                        "Webhook URL rebuild failed user_id=%s guild_id=%s",
                        interaction.user.id,
                        guild_id,
                    )

        if not webhook_url:
            await interaction.followup.send(
                "Rob couldn’t refresh your webhook URL. Please ask staff.",
                ephemeral=True,
            )
            return

        rendered = webhook_setup_card(webhook_url=webhook_url, cog=self)
        await self._edit_or_resend(
            interaction=interaction,
            guild_id=guild_id,
            rendered=rendered,
        )

    async def handle_save_preferences(
        self, interaction: discord.Interaction, view: Any = None
    ) -> None:
        log.info("handle_save_preferences user_id=%s", interaction.user.id)
        guild_id = await self._resolve_dm_onboarding_guild_id(interaction)
        service = self.service
        if guild_id is None:
            await interaction.response.send_message(
                _RERUN_REGISTER_DOMME_MESSAGE, ephemeral=True
            )
            return
        if service is None or not is_new_system_guild(guild_id):
            await interaction.response.send_message(
                "This setup isn't available right now.", ephemeral=True
            )
            return

        leaderboard_access = _read_prefs_from_interaction(interaction, view=view)

        await interaction.response.defer()
        try:
            await service.save_preferences(
                guild_id=guild_id,
                discord_user_id=interaction.user.id,
            )
        except OnboardingError as exc:
            log.warning(
                "save_preferences failed user_id=%s guild_id=%s: %s",
                interaction.user.id,
                guild_id,
                exc,
            )
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        await apply_leaderboard_access(
            self.bot,
            guild_id=guild_id,
            user_id=interaction.user.id,
            enabled=leaderboard_access,
        )

        rendered = success_card(leaderboard_access=leaderboard_access)
        await self._edit_or_resend(
            interaction=interaction,
            guild_id=guild_id,
            rendered=rendered,
        )

    async def handle_migration_save(
        self, interaction: discord.Interaction, view: Any = None
    ) -> None:
        log.info("handle_migration_save user_id=%s", interaction.user.id)
        guild_id = await self._resolve_dm_onboarding_guild_id(interaction)
        if guild_id is None:
            await interaction.response.send_message(
                _RERUN_REGISTER_DOMME_MESSAGE, ephemeral=True
            )
            return
        if not is_new_system_guild(guild_id):
            await interaction.response.send_message(
                "This setup isn't available here.", ephemeral=True
            )
            return

        dommes = getattr(self.bot, "dommes_repo", None)
        if dommes is None:
            await interaction.response.send_message(
                "Preferences aren't available right now.", ephemeral=True
            )
            return

        leaderboard_access = _read_prefs_from_interaction(interaction, view=view)
        await interaction.response.defer()
        try:
            await dommes.set_preferences(
                guild_id=guild_id,
                discord_user_id=interaction.user.id,
                clear_defer=True,
                confirm=True,
            )
        except Exception:
            log.exception(
                "Migration save_preferences failed user_id=%s guild_id=%s",
                interaction.user.id,
                guild_id,
            )
            await interaction.followup.send(
                "Rob couldn’t save those preferences.", ephemeral=True
            )
            return

        await apply_leaderboard_access(
            self.bot,
            guild_id=guild_id,
            user_id=interaction.user.id,
            enabled=leaderboard_access,
        )

        rendered = success_card(leaderboard_access=leaderboard_access)
        try:
            if interaction.message is not None:
                await interaction.message.edit(**rendered.edit_kwargs())
            else:
                await interaction.followup.send(**rendered.send_kwargs())
        except (discord.NotFound, discord.HTTPException):
            log.exception(
                "Migration success card edit failed user_id=%s guild_id=%s",
                interaction.user.id,
                guild_id,
            )

    async def handle_migration_defer(self, interaction: discord.Interaction) -> None:
        log.info("handle_migration_defer user_id=%s", interaction.user.id)
        guild_id = await self._resolve_dm_onboarding_guild_id(interaction)
        if guild_id is None:
            await interaction.response.send_message(
                _RERUN_REGISTER_DOMME_MESSAGE, ephemeral=True
            )
            return
        if not is_new_system_guild(guild_id):
            await interaction.response.send_message(
                "This setup isn't available here.", ephemeral=True
            )
            return

        service = self.service
        if service is None:
            await interaction.response.send_message(
                "Defer isn't available right now.", ephemeral=True
            )
            return

        await interaction.response.defer()
        try:
            await service.defer_migration(
                guild_id=guild_id,
                discord_user_id=interaction.user.id,
                days=7,
            )
        except OnboardingError as exc:
            log.warning(
                "defer_migration failed user_id=%s guild_id=%s: %s",
                interaction.user.id,
                guild_id,
                exc,
            )
            await interaction.followup.send(str(exc), ephemeral=True)
            return

        try:
            await interaction.followup.send(
                "No worries — Rob will check back in with you in 7 days.",
                ephemeral=True,
            )
        except discord.HTTPException:
            log.exception(
                "Failed to follow up after defer_migration user_id=%s",
                interaction.user.id,
            )

    async def handle_migration_open_prefs(
        self, interaction: discord.Interaction
    ) -> None:
        """Deprecated Open Preferences button on stale migration DMs:
        re-render the migration card so the user can proceed."""

        log.info(
            "handle_migration_open_prefs (legacy) user_id=%s", interaction.user.id
        )
        name = getattr(interaction.user, "display_name", None) or interaction.user.name
        rendered = migration_prompt_card(name=name, cog=self)
        try:
            await interaction.response.edit_message(**rendered.edit_kwargs())
        except discord.HTTPException:
            try:
                await interaction.response.send_message(
                    **rendered.send_kwargs(), ephemeral=True
                )
            except discord.HTTPException:
                log.exception("Could not re-send migration prompt.")

    async def on_throne_test_webhook_received(
        self,
        *,
        guild_id: int,
        discord_user_id: int,
    ) -> bool:
        """Called from the bot ops endpoint when a Throne test webhook
        arrives; advances the DM to the preferences card. Returns True if
        the DM was edited."""

        log.info(
            "on_throne_test_webhook_received user_id=%s guild_id=%s",
            discord_user_id,
            guild_id,
        )
        service = self.service
        if service is None or not is_new_system_guild(guild_id):
            log.info(
                "Webhook auto-advance skipped (service=%s new_system_guild=%s) "
                "user_id=%s guild_id=%s",
                service is not None,
                is_new_system_guild(guild_id),
                discord_user_id,
                guild_id,
            )
            return False

        repo = getattr(self.bot, "domme_onboarding_repo", None)
        if repo is None:
            return False
        try:
            state = await repo.get(
                guild_id=guild_id, discord_user_id=discord_user_id
            )
        except Exception:
            log.exception(
                "Auto-advance lookup failed user_id=%s guild_id=%s",
                discord_user_id,
                guild_id,
            )
            return False
        if state is None or state.stage == "completed":
            log.info(
                "Webhook auto-advance no-op user_id=%s guild_id=%s state=%s",
                discord_user_id,
                guild_id,
                getattr(state, "stage", None),
            )
            return False
        try:
            await service.mark_webhook_received(
                guild_id=guild_id, discord_user_id=discord_user_id
            )
        except OnboardingError as exc:
            log.warning(
                "mark_webhook_received refused user_id=%s guild_id=%s: %s",
                discord_user_id,
                guild_id,
                exc,
            )
            return False

        rendered = preferences_card(cog=self)
        edited = await self._edit_stored_dm(
            guild_id=guild_id,
            discord_user_id=discord_user_id,
            rendered=rendered,
        )
        if edited:
            log.info(
                "Webhook auto-advance edited stored DM user_id=%s guild_id=%s",
                discord_user_id,
                guild_id,
            )
        else:
            log.warning(
                "Webhook auto-advance could not edit stored DM "
                "user_id=%s guild_id=%s",
                discord_user_id,
                guild_id,
            )
        return edited

    async def _edit_or_resend(
        self,
        *,
        interaction: discord.Interaction,
        guild_id: int,
        rendered: Any,
    ) -> None:
        """Edit the stored DM in place; if that fails, edit the triggering
        DM or send a fresh DM and update the stored ids."""

        if await self._edit_stored_dm(
            guild_id=guild_id,
            discord_user_id=interaction.user.id,
            rendered=rendered,
        ):
            return

        try:
            if interaction.message is not None and isinstance(
                interaction.channel, discord.DMChannel
            ):
                await interaction.message.edit(**rendered.edit_kwargs())
                await self._persist_dm_message(
                    guild_id=guild_id,
                    discord_user_id=interaction.user.id,
                    message=interaction.message,
                )
                return
        except discord.HTTPException:
            log.exception(
                "Could not edit triggering DM message user_id=%s guild_id=%s",
                interaction.user.id,
                guild_id,
            )

        try:
            message = await interaction.user.send(**rendered.send_kwargs())
            await self._persist_dm_message(
                guild_id=guild_id,
                discord_user_id=interaction.user.id,
                message=message,
            )
        except (discord.Forbidden, discord.HTTPException) as exc:
            log.warning(
                "Fallback DM send failed user_id=%s guild_id=%s: %s",
                interaction.user.id,
                guild_id,
                exc,
            )


def _read_prefs_from_interaction(
    interaction: discord.Interaction,
    view: Any = None,
) -> bool:
    """Read the leaderboard-access selection off the interaction.

    Prefers the live view handed over by the Save button: message components
    only ever carry the original default flags, never the user's live
    selection. Falls back to the interaction's own view, then to scanning
    the message components. Defaults to False.
    """

    leaderboard_access = False

    if view is None:
        view = getattr(interaction, "view", None)
    if isinstance(view, (PreferencesView, MigrationPromptView)):
        return view.chosen_leaderboard_access

    message = getattr(interaction, "message", None)
    if message is None:
        return leaderboard_access

    def _match_select(item: Any) -> None:
        nonlocal leaderboard_access
        custom_id = getattr(item, "custom_id", None)
        if custom_id in (
            ID_PREFS_LEADERBOARD_ACCESS,
            ID_MIGRATION_LEADERBOARD_ACCESS,
        ):
            values = getattr(item, "values", []) or []
            if values:
                leaderboard_access = values[0] == LEADERBOARD_ACCESS_ON_VALUE

    for row in getattr(message, "components", []) or []:
        for child in getattr(row, "children", []) or []:
            # Legacy layout: select as a direct container child.
            _match_select(child)
            # Current layout: select inside an ActionRow inside the container.
            for grandchild in getattr(child, "children", []) or []:
                _match_select(grandchild)
    return leaderboard_access


async def setup(bot: "RobBot") -> None:
    cog = DMOnboardingCog(bot)
    await bot.add_cog(cog)
    cog.register_persistent_views()
