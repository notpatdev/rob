from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands, tasks

from rob.config.guilds import is_new_system_guild
from rob.database.repositories.models import GuildSettings, ServerBackupApproval
from rob.discord.permissions import is_staff_member, member_has_role
from rob.services.server_backup_service import BackupCycleResult
from rob.ui.cards.errors import error_card
from rob.ui.cards.server_backup import backup_decision_card, major_change_approval_card
from rob.ui.render import add_card_actions

if TYPE_CHECKING:
    from rob.discord.client import RobBot

log = logging.getLogger(__name__)


def _change_lines(approval: ServerBackupApproval) -> list[str]:
    return [str(change.get("detail", "")) for change in approval.changes if change.get("detail")]


class _BackupDecisionButton(discord.ui.Button):
    def __init__(self, *, cog: "ServerBackupCog", approval_id: int, approve: bool) -> None:
        action = "approve" if approve else "reject"
        super().__init__(
            label="Approve" if approve else "Reject",
            style=discord.ButtonStyle.success if approve else discord.ButtonStyle.danger,
            custom_id=f"backup-approval:{action}:{approval_id}",
        )
        self.cog = cog
        self.approval_id = approval_id
        self.approve = approve

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.cog.handle_decision(interaction, approval_id=self.approval_id, approve=self.approve)


class ServerBackupCog(commands.Cog):
    def __init__(self, bot: "RobBot") -> None:
        self.bot = bot
        self.backup_loop.change_interval(minutes=max(1, bot.settings.server_backup_loop_minutes))
        self.backup_loop.start()

    def cog_unload(self) -> None:
        self.backup_loop.cancel()

    # -- loop -----------------------------------------------------------------

    @tasks.loop(minutes=60)
    async def backup_loop(self) -> None:
        guild_ids = await self.bot.guild_settings_repo.list_guild_ids()
        for guild_id in guild_ids:
            if not is_new_system_guild(guild_id):
                continue
            guild = self.bot.get_guild(guild_id)
            if guild is None:
                continue
            try:
                result = await self.bot.server_backup_service.run_cycle(guild)
                await self._handle_cycle_result(guild, result)
            except Exception:  # pragma: no cover - safety logging around runtime loop
                log.exception("Server backup loop failed for guild_id=%s", guild_id)

    @backup_loop.before_loop
    async def _before_backup_loop(self) -> None:
        await self.bot.wait_until_ready()

    async def run_once(self, guild: discord.Guild) -> BackupCycleResult:
        """Run a single backup cycle now (used by rob backup run)."""

        result = await self.bot.server_backup_service.run_cycle(guild)
        await self._handle_cycle_result(guild, result)
        return result

    async def _handle_cycle_result(self, guild: discord.Guild, result: BackupCycleResult) -> None:
        if result.action == "needs_approval" and result.approval is not None:
            await self._post_approval(guild, result.approval)
        elif result.action == "blocked" and result.approval is not None:
            # A change is still awaiting approval. Make sure the prompt is live.
            if result.approval.message_id is None:
                await self._post_approval(guild, result.approval)
        elif result.action == "suppressed":
            log.info(
                "Server backup suppressed re-prompt of a rejected change for guild_id=%s",
                guild.id,
            )

    # -- startup rebind -------------------------------------------------------

    async def rebind_pending_views(self) -> None:
        for approval in await self.bot.server_backup_service.backups.list_pending_approvals():
            if approval.message_id is None:
                continue
            self.bot.add_view(self._build_view(approval.id), message_id=approval.message_id)

    # -- posting --------------------------------------------------------------

    def _approval_buttons(self, approval_id: int) -> tuple[discord.ui.Button, discord.ui.Button]:
        return (
            _BackupDecisionButton(cog=self, approval_id=approval_id, approve=True),
            _BackupDecisionButton(cog=self, approval_id=approval_id, approve=False),
        )

    def _build_view(self, approval_id: int) -> discord.ui.LayoutView:
        view = discord.ui.LayoutView(timeout=None)
        add_card_actions(view, *self._approval_buttons(approval_id))
        return view

    def _mod_mentions(self, settings: GuildSettings | None) -> tuple[str, discord.AllowedMentions]:
        role_ids: list[int] = []
        labels: list[str] = []
        mod_role_id = settings.mod_role_id if settings is not None else None
        trial_role_id = settings.trial_mod_role_id if settings is not None else None
        if mod_role_id is not None:
            role_ids.append(mod_role_id)
            labels.append(f"<@&{mod_role_id}>")
        if trial_role_id is not None:
            role_ids.append(trial_role_id)
            labels.append(f"<@&{trial_role_id}>")
        mentions_text = " ".join(labels) if labels else "moderators"
        allowed = discord.AllowedMentions(
            everyone=False,
            users=False,
            roles=[discord.Object(id=rid) for rid in role_ids] if role_ids else False,
        )
        return mentions_text, allowed

    def _render_approval(self, approval: ServerBackupApproval, settings: GuildSettings | None):
        mod_mentions, _allowed = self._mod_mentions(settings)
        view = discord.ui.LayoutView(timeout=None)
        rendered = major_change_approval_card(
            change_lines=_change_lines(approval),
            mod_mentions=mod_mentions,
            approvals=approval.approved_by,
            required_approvals=approval.required_approvals,
            view=view,
        )
        add_card_actions(view, *self._approval_buttons(approval.id))
        return rendered

    async def _post_approval(self, guild: discord.Guild, approval: ServerBackupApproval) -> None:
        settings = await self.bot.guild_settings_repo.get(guild.id)
        channel_id = settings.backup_approval_channel_id if settings is not None else None
        channel = guild.get_channel(channel_id) if channel_id is not None else None
        if channel is None or not isinstance(channel, discord.abc.Messageable):
            log.warning(
                "Server backup approval channel is not configured/available for guild_id=%s; "
                "auto-adopting change without the moderator gate.",
                guild.id,
            )
            await self.bot.server_backup_service.force_adopt(
                approval_id=approval.id,
                reason="No backup approval channel configured.",
            )
            return

        _mentions_text, allowed = self._mod_mentions(settings)
        rendered = self._render_approval(approval, settings)
        try:
            message = await channel.send(**rendered.send_kwargs(), allowed_mentions=allowed)
        except discord.HTTPException:
            log.exception("Failed to post server backup approval for guild_id=%s", guild.id)
            await self.bot.server_backup_service.force_adopt(
                approval_id=approval.id,
                reason="Could not deliver the approval prompt.",
            )
            return

        await self.bot.server_backup_service.backups.set_delivery(
            approval_id=approval.id,
            channel_id=message.channel.id,
            message_id=message.id,
        )
        self.bot.add_view(self._build_view(approval.id), message_id=message.id)

    # -- decisions ------------------------------------------------------------

    def _is_moderator(self, member: discord.abc.User | None, settings: GuildSettings | None) -> bool:
        if is_staff_member(member, settings):
            return True
        trial_role_id = settings.trial_mod_role_id if settings is not None else None
        return member_has_role(member, trial_role_id)

    async def handle_decision(
        self,
        interaction: discord.Interaction,
        *,
        approval_id: int,
        approve: bool,
    ) -> None:
        guild = interaction.guild
        settings = await self.bot.guild_settings_repo.get(guild.id) if guild is not None else None
        member = interaction.user
        if guild is not None and not isinstance(member, discord.Member):
            member = guild.get_member(interaction.user.id) or interaction.user
        if not self._is_moderator(member, settings):
            await interaction.response.send_message(
                **error_card("Only moderators can approve or reject server backup changes.").send_kwargs(),
                ephemeral=True,
            )
            return

        service = self.bot.server_backup_service
        try:
            if approve:
                decision = await service.register_approval(
                    approval_id=approval_id, approver_user_id=interaction.user.id
                )
            else:
                decision = await service.reject(
                    approval_id=approval_id, rejected_by_user_id=interaction.user.id
                )
        except Exception:
            log.exception("Server backup decision failed approval_id=%s", approval_id)
            await interaction.response.send_message(
                **error_card("Rob could not record that decision just now.").send_kwargs(),
                ephemeral=True,
            )
            return

        if decision.status == "gone":
            await interaction.response.send_message(
                **error_card("This approval is no longer pending.").send_kwargs(),
                ephemeral=True,
            )
            return
        if decision.status == "duplicate":
            await interaction.response.send_message(
                **error_card(
                    "You've already approved this change.",
                    f"{decision.remaining} more moderator approval(s) needed.",
                ).send_kwargs(),
                ephemeral=True,
            )
            return

        approval = decision.approval
        if approval is None:
            await interaction.response.send_message(
                **error_card("This approval is no longer available.").send_kwargs(),
                ephemeral=True,
            )
            return

        if decision.status == "recorded":
            # Progress update: refresh the prompt with the new approval count.
            rendered = self._render_approval(approval, settings)
            await interaction.response.edit_message(**rendered.edit_kwargs())
            return

        approved = decision.status == "approved"
        rendered = backup_decision_card(
            approved=approved,
            change_lines=_change_lines(approval),
            decided_by_user_id=interaction.user.id,
            approvals=approval.approved_by,
            required_approvals=approval.required_approvals,
        )
        await interaction.response.edit_message(**rendered.edit_kwargs())
