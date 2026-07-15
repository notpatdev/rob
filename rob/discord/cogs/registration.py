from __future__ import annotations

from typing import TYPE_CHECKING
import inspect
import logging

import discord
from discord import app_commands
from discord.ext import commands

from rob.config.guilds import MAIN_GUILD_ID, TEST_GUILD_ID, is_new_system_guild
from rob.discord.permissions import member_has_role
from rob.ui.cards.errors import error_card, error_permission
from rob.ui.cards.registration import registration_card, throne_setup_card
from rob.ui.copy import (
    PERMISSION_ROLE_MISSING,
    PERMISSION_ROLE_NOT_CONFIGURED,
    throne_setup_steps,
)
from rob.ui.render import add_card_actions
from rob.utils.text import collapse_whitespace

if TYPE_CHECKING:
    from rob.discord.client import RobBot


log = logging.getLogger(__name__)

_RESERVED_SUB_NAMES = {"anonymous", "anon", "private", "hidden"}


def _dm_blocked_error_card():
    return error_card(
        "You're registered, but Rob couldn't DM you.",
        "This usually means Discord blocked the DM. Please check:\n\n"
        "- Server Privacy Settings → Allow direct messages from server members\n"
        "- You have not blocked this bot\n"
        "- Your Discord privacy settings allow bot/member DMs\n\n"
        "Once fixed, run /register domme again.",
    )


class YesButton(discord.ui.Button):
    def __init__(self, domme_id: int, send_track_channel_id: int | None) -> None:
        super().__init__(label="Yes", style=discord.ButtonStyle.success)
        self.domme_id = domme_id
        self.send_track_channel_id = send_track_channel_id

    async def callback(self, interaction: discord.Interaction) -> None:
        bot = interaction.client
        domme = await bot.dommes_repo.get(self.domme_id)
        if domme and (domme.webhook_connected_at or domme.last_successful_event_at):
            destination = f"<#{self.send_track_channel_id}>" if self.send_track_channel_id else "the send tracking channel"
            success_msg = registration_card(
                title="Rob | Dom/me Setup",
                summary=(
                    "Your Throne profile has been saved and your webhook is now connected.\n\n"
                    f"Your Throne sends will appear in {destination} as soon as Rob receives them."
                ),
                details=[
                    (
                        "What Rob collects",
                        "- Your Discord user ID\n"
                        "- Your Throne handle and creator ID\n"
                        "- Public wishlist item names and prices\n"
                        "- Item images when Throne includes them\n"
                        "- Send amounts and sender names from webhook events\n"
                        "- Webhook delivery status for troubleshooting",
                    ),
                    (
                        "How Rob uses it",
                        "- To post send notifications in the configured channel\n"
                        "- To update Dom/me and Sub leaderboards\n"
                        "- To prevent duplicate webhook events from counting twice\n"
                        "- To help rotate or rebuild your webhook URL when needed",
                    ),
                    (
                        "Important notes",
                        "- Rob never needs your Throne password\n"
                        "- Your webhook URL should be treated like a secret\n"
                        "- If you think the URL was shared, ask staff to rebuild it",
                    ),
                ],
                footer="Setup complete. No response needed here unless you want to rerun setup later.",
            )
            await interaction.response.edit_message(**success_msg.edit_kwargs())
            return

        msg = throne_setup_card(
            "Not seeing it yet.\n\nPlease make sure you clicked Save Settings in Throne, then click Test Webhook again. "
            "Once Throne shows a success message, press Yes here again."
        )
        add_card_actions(msg.view, YesButton(self.domme_id, self.send_track_channel_id), NotYetButton())
        await interaction.response.edit_message(**msg.edit_kwargs())


class NotYetButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Not Yet", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()


class RegistrationCog(commands.Cog):
    register_group = app_commands.Group(
        name="register",
        description="Register as a Dom/me or Sub.",
        guild_ids=[MAIN_GUILD_ID, TEST_GUILD_ID],
    )

    def __init__(self, bot: RobBot) -> None:
        self.bot = bot
        self._active_domme_submission_keys: set[tuple[int, int]] = set()

    async def _require_configured_role(
        self,
        interaction: discord.Interaction,
        *,
        role_id: int | None,
    ) -> bool:
        if role_id is None:
            await interaction.response.send_message(
                **error_permission(PERMISSION_ROLE_NOT_CONFIGURED).send_kwargs(),
                ephemeral=True,
            )
            return False
        if not member_has_role(interaction.user, role_id):
            await interaction.response.send_message(
                **error_permission(PERMISSION_ROLE_MISSING).send_kwargs(),
                ephemeral=True,
            )
            return False
        return True

    async def _registrations_blocked_for_guild(self, guild_id: int | None) -> bool:
        maintenance = getattr(self.bot, "maintenance_service", None)
        if maintenance is None:
            return False
        # Wind-down phase 1+ closes new registrations.
        phase_getter = getattr(maintenance, "get_wind_down_phase", None)
        if phase_getter is not None:
            phase_result = phase_getter()
            if inspect.isawaitable(phase_result) and int(await phase_result) >= 1:
                return True
        checker = getattr(maintenance, "registrations_blocked_for_guild", None)
        if checker is not None:
            result = checker(guild_id)
            if inspect.isawaitable(result):
                return bool(await result)
        legacy_checker = getattr(maintenance, "registrations_blocked", None)
        if legacy_checker is not None:
            result = legacy_checker()
            if inspect.isawaitable(result):
                return bool(await result)
            if isinstance(result, bool):
                return result
        return False

    async def _require_registration_available(self, interaction: discord.Interaction) -> bool:
        if not await self._registrations_blocked_for_guild(interaction.guild.id if interaction.guild else None):
            return True
        await interaction.response.send_message(
            **error_card(
                "Rob is under maintenance right now.",
                "Dom/me and Sub registration are paused until the maintenance window is over. Counting and approved manual send work can still continue.",
            ).send_kwargs(),
            ephemeral=True,
        )
        return False

    @register_group.command(name="domme", description="Register a Dom/me Throne profile.")
    async def register_domme(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.user is None:
            await interaction.response.send_message(**error_card("This command can only be used in a server.").send_kwargs(), ephemeral=True)
            return
        if not await self._require_registration_available(interaction):
            return
        log.info(
            "/register domme invoked guild_id=%s discord_user_id=%s",
            interaction.guild.id,
            interaction.user.id,
        )

        settings = await self.bot.guild_settings_repo.get(interaction.guild.id)
        if not await self._require_configured_role(
            interaction,
            role_id=settings.domme_role_id if settings is not None else None,
        ):
            return

        # ------------------------------------------------------------------
        # NEW-SYSTEM GUILDS (main + test): route to the new DM-first
        # onboarding flow. Anywhere else we keep using the existing
        # "Continue Setup" + webhook yes/not-yet flow below, unchanged.
        # ------------------------------------------------------------------
        if is_new_system_guild(interaction.guild.id):
            dm_cog = self.bot.get_cog("DMOnboardingCog")
            if dm_cog is not None:
                ok, _message, error_text = await dm_cog.start_onboarding_dm(
                    user=interaction.user,
                    guild_id=interaction.guild.id,
                )
                if ok:
                    await interaction.response.send_message(
                        **registration_card(
                            title="Rob | Setup Sent",
                            summary=(
                                "I've sent your setup to your DMs. Open that "
                                "message and tap **Enter Throne details** to begin."
                            ),
                        ).send_kwargs(),
                        ephemeral=True,
                    )
                else:
                    await interaction.response.send_message(
                        **_dm_blocked_error_card().send_kwargs(),
                        ephemeral=True,
                    )
                    if error_text:
                        log.info(
                            "DM onboarding start failed guild_id=%s user_id=%s: %s",
                            interaction.guild.id,
                            interaction.user.id,
                            error_text,
                        )
                return
            log.warning(
                "Test guild /register domme: DMOnboardingCog not loaded, "
                "falling back to legacy flow."
            )

        try:
            invite = registration_card(
                title="Rob | Dom/me Setup",
                summary=(
                    "Let's get your Throne profile connected to Rob.\n"
                    "Rob will use it to match sends, keep leaderboard data tidy, and build your webhook setup steps."
                ),
                details=[("Next step", "Click **Continue Setup** and submit your Throne username or profile URL.")],
            )
            add_card_actions(
                invite.view,
                _DommeSetupStartButton(
                    cog=self,
                    guild_id=interaction.guild.id,
                    discord_user_id=interaction.user.id,
                    send_track_channel_id=settings.send_track_channel_id if settings else None,
                ),
            )
            await interaction.user.send(**invite.send_kwargs())
            log.info(
                "Dom/me DM setup card sent guild_id=%s discord_user_id=%s",
                interaction.guild.id,
                interaction.user.id,
            )
        except discord.Forbidden as exc:
            log.warning("Failed to DM Throne setup flow to user_id=%s guild_id=%s reason=Forbidden status=%s code=%s text=%s", interaction.user.id, interaction.guild.id if interaction.guild else None, getattr(exc, "status", None), getattr(exc, "code", None), getattr(exc, "text", None))
            await interaction.response.send_message(**_dm_blocked_error_card().send_kwargs(), ephemeral=True)
            return
        except discord.HTTPException as exc:
            log.exception("Failed to DM Throne setup flow to user_id=%s guild_id=%s status=%s code=%s text=%s", interaction.user.id, interaction.guild.id if interaction.guild else None, getattr(exc, "status", None), getattr(exc, "code", None), getattr(exc, "text", None))
            if getattr(exc, "status", None) == 400 and getattr(exc, "code", None) == 50035:
                await interaction.response.send_message(**error_card("Rob hit a setup-message error.", "Rob couldn't generate the setup DM correctly. Staff have enough detail in logs to fix it.").send_kwargs(), ephemeral=True)
                return
            await interaction.response.send_message(**_dm_blocked_error_card().send_kwargs(), ephemeral=True)
            return

        await interaction.response.send_message(
            **registration_card(
                title="Rob | Setup Sent",
                summary="I've sent your setup to DMs. Open that message and press **Continue Setup**.",
            ).send_kwargs(),
            ephemeral=True,
        )

    async def _complete_domme_registration(
        self,
        *,
        interaction: discord.Interaction,
        guild_id: int,
        discord_user_id: int,
        send_track_channel_id: int | None,
        throne_input: str,
        setup_message_id: int | None = None,
    ) -> None:
        if await self._registrations_blocked_for_guild(guild_id):
            await interaction.followup.send(
                **error_card(
                    "Rob is under maintenance right now.",
                    "Dom/me registration is paused until the maintenance window is over.",
                ).send_kwargs()
            )
            return
        try:
            result = await self.bot.registration_service.register_domme(
                guild_id=guild_id,
                discord_user_id=discord_user_id,
                throne_input=throne_input,
            )
            domme_result = getattr(result, "domme", None) or getattr(result, "creator", None)
            log.info(
                "Dom/me registration upserted guild_id=%s discord_user_id=%s domme_id=%s",
                guild_id,
                discord_user_id,
                getattr(domme_result, "id", None),
            )
        except ValueError as exc:
            await interaction.followup.send(**error_card("Dom/me registration could not be completed.", str(exc)).send_kwargs())
            return

        if not result.webhook_url:
            await interaction.followup.send(
                **error_card(
                    "Webhook URL setup is unavailable.",
                    "Ask staff to verify THRONE_WEBHOOK_BASE_URL on the bot server.",
                ).send_kwargs()
            )
            return

        domme_result = getattr(result, "domme", None) or getattr(result, "creator", None)
        if domme_result is None or getattr(domme_result, "id", None) is None:
            await interaction.followup.send(
                **error_card(
                    "Dom/me registration completed, but setup could not continue.",
                    "Rob could not resolve the registration record needed for setup buttons. Please ask staff to check the logs.",
                ).send_kwargs()
            )
            return

        dm_msg = throne_setup_card(throne_setup_steps(result.webhook_url))
        add_card_actions(dm_msg.view, YesButton(int(domme_result.id), send_track_channel_id), NotYetButton())
        if setup_message_id is not None and interaction.channel is not None:
            setup_message = interaction.channel.get_partial_message(setup_message_id)
            await setup_message.edit(**dm_msg.edit_kwargs())
        else:
            await interaction.followup.send(**dm_msg.send_kwargs())
        log.info(
            "Dom/me webhook setup card edited guild_id=%s discord_user_id=%s domme_id=%s",
            guild_id,
            discord_user_id,
            int(domme_result.id),
        )

    @register_group.command(name="sub", description="Register a sending name to claim sends.")
    async def register_sub(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or interaction.user is None:
            await interaction.response.send_message(**error_card("This command can only be used in a server.").send_kwargs(), ephemeral=True)
            return
        if not await self._require_registration_available(interaction):
            return

        settings = await self.bot.guild_settings_repo.get(interaction.guild.id)
        if not await self._require_configured_role(
            interaction,
            role_id=settings.sub_role_id if settings is not None else None,
        ):
            return

        await interaction.response.send_modal(
            _SubRegistrationModal(
                cog=self,
                guild_id=interaction.guild.id,
                discord_user_id=interaction.user.id,
            )
        )


class _DommeSetupStartButton(discord.ui.Button):
    def __init__(
        self,
        *,
        cog: RegistrationCog,
        guild_id: int,
        discord_user_id: int,
        send_track_channel_id: int | None,
    ) -> None:
        super().__init__(label="Continue Setup", style=discord.ButtonStyle.primary)
        self.cog = cog
        self.guild_id = guild_id
        self.discord_user_id = discord_user_id
        self.send_track_channel_id = send_track_channel_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user is None or interaction.user.id != self.discord_user_id:
            await interaction.response.send_message(
                **error_card("This setup flow belongs to someone else.").send_kwargs(),
                ephemeral=True,
            )
            return
        log.info(
            "Dom/me setup modal opened guild_id=%s discord_user_id=%s",
            self.guild_id,
            self.discord_user_id,
        )
        setup_message_id = interaction.message.id if interaction.message is not None else None
        await interaction.response.send_modal(
            _DommeRegistrationModal(
                cog=self.cog,
                guild_id=self.guild_id,
                discord_user_id=self.discord_user_id,
                send_track_channel_id=self.send_track_channel_id,
                setup_message_id=setup_message_id,
            )
        )


class _DommeRegistrationModal(discord.ui.Modal, title="Rob | Dom/me Setup"):
    throne = discord.ui.TextInput(
        label="Throne username or profile URL",
        placeholder="example: missadore or https://throne.com/missadore",
        required=True,
        max_length=200,
    )

    def __init__(
        self,
        *,
        cog: RegistrationCog,
        guild_id: int,
        discord_user_id: int,
        send_track_channel_id: int | None,
        setup_message_id: int | None,
    ) -> None:
        super().__init__(timeout=600)
        self.cog = cog
        self.guild_id = guild_id
        self.discord_user_id = discord_user_id
        self.send_track_channel_id = send_track_channel_id
        self.setup_message_id = setup_message_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user is None or interaction.user.id != self.discord_user_id:
            await interaction.response.send_message(
                **error_card("This setup flow belongs to someone else.").send_kwargs(),
                ephemeral=True,
            )
            return

        flow_key = (self.guild_id, self.discord_user_id)
        if flow_key in self.cog._active_domme_submission_keys:
            await interaction.response.send_message(
                **error_card(
                    "Dom/me setup is already being processed.",
                    "Give Rob a second to finish the previous submission.",
                ).send_kwargs(),
                ephemeral=True,
            )
            return

        self.cog._active_domme_submission_keys.add(flow_key)
        log.info(
            "Dom/me modal submitted guild_id=%s discord_user_id=%s",
            self.guild_id,
            self.discord_user_id,
        )
        await interaction.response.defer()
        try:
            await self.cog._complete_domme_registration(
                interaction=interaction,
                guild_id=self.guild_id,
                discord_user_id=self.discord_user_id,
                send_track_channel_id=self.send_track_channel_id,
                throne_input=str(self.throne.value),
                setup_message_id=self.setup_message_id,
            )
        finally:
            self.cog._active_domme_submission_keys.discard(flow_key)


class _SubRegistrationModal(discord.ui.Modal, title="Rob | Sub Registration"):
    send_name_1 = discord.ui.TextInput(
        label="Throne username / send name 1",
        placeholder="Required",
        required=True,
        max_length=120,
    )
    send_name_2 = discord.ui.TextInput(
        label="Throne username / send name 2",
        placeholder="Optional",
        required=False,
        max_length=120,
    )
    send_name_3 = discord.ui.TextInput(
        label="Throne username / send name 3",
        placeholder="Optional",
        required=False,
        max_length=120,
    )

    def __init__(
        self,
        *,
        cog: RegistrationCog,
        guild_id: int,
        discord_user_id: int,
    ) -> None:
        super().__init__(timeout=600)
        self.cog = cog
        self.guild_id = guild_id
        self.discord_user_id = discord_user_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user is None or interaction.user.id != self.discord_user_id:
            await interaction.response.send_message(
                **error_card("This registration flow belongs to someone else.").send_kwargs(),
                ephemeral=True,
            )
            return

        raw_names = [
            str(self.send_name_1.value),
            str(self.send_name_2.value),
            str(self.send_name_3.value),
        ]
        cleaned = [collapse_whitespace(value.strip()) for value in raw_names if value and value.strip()]
        if not cleaned:
            await interaction.response.send_message(
                **error_card("Sub registration could not be completed.", "At least one sending name is required.").send_kwargs(),
                ephemeral=True,
            )
            return
        if len(cleaned) > 3:
            await interaction.response.send_message(
                **error_card("Sub registration could not be completed.", "You can register up to 3 sending names.").send_kwargs(),
                ephemeral=True,
            )
            return

        seen: set[str] = set()
        for name in cleaned:
            lowered = name.casefold()
            if lowered in seen:
                await interaction.response.send_message(
                    **error_card("Sub registration could not be completed.", "Duplicate sending names are not allowed.").send_kwargs(),
                    ephemeral=True,
                )
                return
            if lowered in _RESERVED_SUB_NAMES:
                await interaction.response.send_message(
                    **error_card("Sub registration could not be completed.", f"'{name}' is reserved and cannot be claimed.").send_kwargs(),
                    ephemeral=True,
                )
                return
            seen.add(lowered)

        await interaction.response.defer(ephemeral=True)
        if await self.cog._registrations_blocked_for_guild(self.guild_id):
            await interaction.followup.send(
                **error_card(
                    "Rob is under maintenance right now.",
                    "Sub registration is paused until the maintenance window is over.",
                ).send_kwargs(),
                ephemeral=True,
            )
            return
        try:
            result = await self.cog.bot.registration_service.register_sub(
                guild_id=self.guild_id,
                discord_user_id=self.discord_user_id,
                send_names=cleaned,
            )
        except ValueError as exc:
            await interaction.followup.send(
                **error_card("Sub registration could not be completed.", str(exc)).send_kwargs(),
                ephemeral=True,
            )
            return

        details = [(f"Tracked Name {idx + 1}", name) for idx, name in enumerate(result.send_names)]
        await interaction.followup.send(
            **registration_card(
                title="Rob | Sub Registered",
                summary="Your send-claim names are now active.",
                details=details,
            ).send_kwargs(),
            ephemeral=True,
        )
