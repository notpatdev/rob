"""Global guild-context ``/profile`` command and private onboarding entry points."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import discord
from discord import app_commands
from discord.ext import commands

from bill.components.profile import profile_wizard_view
from bill.components.public_profile import public_profile_view
from bill.worker_client import DraftScope, ProfileDraft, ServerProfileMode, WorkerAPIError

if TYPE_CHECKING:
    from bill.bot import BillBot

PROFILE_MISSING_PROMPT = "You don't have a Bill profile here yet. Would you like to set one up?"
DM_CLOSED_PROMPT = (
    "I couldn't DM you. Please enable direct messages from server members, then try again."
)
RESUME_PROMPT = "You have a saved Bill profile draft. Would you like to resume it or restart?"


class ProfilePromptView(discord.ui.View):
    """Normal ephemeral entry prompt: V2 begins only after the private DM intro."""

    def __init__(self, cog: ProfileCog, *, server_choice: bool = False) -> None:
        super().__init__(timeout=180)
        self.cog, self.server_choice = cog, server_choice

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def start(
        self, interaction: discord.Interaction[discord.Client], _: discord.ui.Button
    ) -> None:
        await self.cog.start_profile(interaction, server_mode=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(
        self, interaction: discord.Interaction[discord.Client], _: discord.ui.Button
    ) -> None:
        await interaction.response.edit_message(
            content="No problem — your profile has not been changed.", view=None
        )


class GlobalChoiceView(discord.ui.View):
    def __init__(self, cog: ProfileCog) -> None:
        super().__init__(timeout=180)
        self.cog = cog

    @discord.ui.button(label="Use global profile", style=discord.ButtonStyle.success)
    async def linked(
        self, interaction: discord.Interaction[discord.Client], _: discord.ui.Button
    ) -> None:
        await self.cog.start_profile(interaction, server_mode=ServerProfileMode.LINKED)

    @discord.ui.button(
        label="Create a separate server profile", style=discord.ButtonStyle.secondary
    )
    async def independent(
        self, interaction: discord.Interaction[discord.Client], _: discord.ui.Button
    ) -> None:
        await self.cog.start_profile(interaction, server_mode=ServerProfileMode.INDEPENDENT)


class ResumeDraftView(discord.ui.View):
    """A resumed draft is never DM'd until the owner explicitly chooses it."""

    def __init__(self, cog: ProfileCog, draft: ProfileDraft) -> None:
        super().__init__(timeout=180)
        self.cog, self.draft = cog, draft

    @discord.ui.button(label="Resume draft", style=discord.ButtonStyle.success)
    async def resume(
        self, interaction: discord.Interaction[discord.Client], _: discord.ui.Button
    ) -> None:
        await self.cog.deliver_draft(interaction, self.draft, resumed=True)

    @discord.ui.button(label="Restart draft", style=discord.ButtonStyle.danger)
    async def restart(
        self, interaction: discord.Interaction[discord.Client], _: discord.ui.Button
    ) -> None:
        await interaction.response.edit_message(
            content="Restart this saved draft? Unsaved progress will be replaced.",
            view=ResumeRestartConfirmView(self.cog, self.draft),
        )


class ResumeRestartConfirmView(discord.ui.View):
    def __init__(self, cog: ProfileCog, draft: ProfileDraft) -> None:
        super().__init__(timeout=90)
        self.cog, self.draft = cog, draft

    @discord.ui.button(label="Restart draft", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction[discord.Client], _: discord.ui.Button
    ) -> None:
        try:
            restarted = await self.cog.bot.require_worker().restart_draft(
                self.draft.id,
                owner_user_id=interaction.user.id,
                expected_revision=self.draft.revision,
            )
        except WorkerAPIError as exc:
            await interaction.response.edit_message(
                content=f"Bill could not restart your draft: {exc}",
                view=None,
            )
            return
        await self.cog.deliver_draft(interaction, restarted, resumed=False)

    @discord.ui.button(label="Keep draft", style=discord.ButtonStyle.success)
    async def cancel(
        self, interaction: discord.Interaction[discord.Client], _: discord.ui.Button
    ) -> None:
        await interaction.response.edit_message(content="Your saved draft is unchanged.", view=None)


class ProfileCog(commands.Cog):
    """Routes lookups without ever exposing drafts, webhook URLs, or secrets publicly."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = cast("BillBot", bot)

    @app_commands.command(name="profile", description="View a Bill profile in this server")
    @app_commands.guild_only()
    @app_commands.describe(member="The member whose profile you want to view")
    async def profile(
        self, interaction: discord.Interaction[discord.Client], member: discord.Member | None = None
    ) -> None:
        target = member or interaction.user
        try:
            lookup = await self.bot.require_worker().get_profile(
                guild_id=interaction.guild_id, user_id=target.id
            )
        except WorkerAPIError:
            await interaction.response.send_message(
                "Bill could not load that profile right now.", ephemeral=True
            )
            return
        if lookup.profile is not None:
            await interaction.response.send_message(
                view=public_profile_view(
                    lookup.profile,
                    guild_id=interaction.guild_id or 0,
                    owner_view=target.id == interaction.user.id,
                    display_name=target.display_name,
                )
            )
            return
        if member is not None and member.id != interaction.user.id:
            await interaction.response.send_message(
                f"{member.mention} does not have an applicable Bill profile here.", ephemeral=True
            )
            return
        if interaction.guild_id != self.bot.settings.home_guild_id and lookup.global_available:
            await interaction.response.send_message(
                "You have a global Bill profile. Choose how to use it in this server.",
                view=GlobalChoiceView(self),
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            PROFILE_MISSING_PROMPT, view=ProfilePromptView(self), ephemeral=True
        )

    async def start_profile(
        self,
        interaction: discord.Interaction[discord.Client],
        *,
        server_mode: ServerProfileMode | None,
    ) -> None:
        """Create/resume Worker state, then deliver normal DM intro before V2 wizard."""
        if interaction.guild_id is None:
            await interaction.response.send_message(
                "Profiles can only be started from a server.", ephemeral=True
            )
            return
        scope = (
            DraftScope.GLOBAL
            if interaction.guild_id == self.bot.settings.home_guild_id
            else DraftScope.SERVER
        )
        if scope is DraftScope.SERVER and server_mode is None:
            await interaction.response.send_message(
                "Choose whether to use your global profile or create a separate server profile.",
                view=GlobalChoiceView(self),
                ephemeral=True,
            )
            return
        try:
            started = await self.bot.require_worker().start_draft(
                owner_user_id=interaction.user.id,
                origin_guild_id=interaction.guild_id,
                target_scope=scope,
                guild_id=interaction.guild_id if scope is DraftScope.SERVER else None,
                server_mode=server_mode,
            )
        except discord.Forbidden:
            await interaction.response.edit_message(content=DM_CLOSED_PROMPT, view=None)
            return
        except WorkerAPIError as exc:
            await interaction.response.send_message(
                f"Bill could not start your profile: {exc}", ephemeral=True
            )
            return
        if started.resume_required:
            await interaction.response.edit_message(
                content=RESUME_PROMPT,
                view=ResumeDraftView(self, started.draft),
            )
            return
        await self.deliver_draft(interaction, started.draft, resumed=False)

    async def deliver_draft(
        self,
        interaction: discord.Interaction[discord.Client],
        draft: ProfileDraft,
        *,
        resumed: bool,
    ) -> None:
        """DM after opt-in; Worker state remains recoverable when DMs are closed."""
        try:
            dm = await interaction.user.create_dm()
            await dm.send(
                "Welcome to Bill profile setup. Your progress is private and saved automatically."
            )
            await dm.send(view=profile_wizard_view(draft))
        except discord.Forbidden:
            if interaction.response.is_done():
                await interaction.followup.send(
                    f"{DM_CLOSED_PROMPT} Your saved draft can be resumed later.",
                    ephemeral=True,
                )
            else:
                await interaction.response.edit_message(
                    content=f"{DM_CLOSED_PROMPT} Your saved draft can be resumed later.",
                    view=None,
                )
            return
        text = "I sent your private profile wizard in a DM."
        if resumed:
            text = "I sent your saved private profile wizard in a DM."
        if interaction.response.is_done():
            await interaction.followup.send(text, ephemeral=True)
        else:
            await interaction.response.edit_message(content=text, view=None)
