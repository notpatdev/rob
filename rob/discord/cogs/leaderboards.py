from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from rob.config.guilds import MAIN_GUILD_ID, TEST_GUILD_ID, is_new_system_guild
from rob.discord.permissions import is_staff_member
from rob.ui.cards.errors import error_card, error_permission
from rob.ui.cards.stats import (
    DommeStatsCardData,
    SubStatsCardData,
    leaderboard_personal_stats_card,
)
from rob.ui.copy import PERMISSION_LEADERBOARD_ROLE_MISSING

if TYPE_CHECKING:
    from rob.discord.client import RobBot


class LeaderboardsCog(commands.Cog):
    def __init__(self, bot: RobBot) -> None:
        self.bot = bot

    @app_commands.command(name="leaderboard", description="Show send stats for yourself or another member.")
    @app_commands.guilds(MAIN_GUILD_ID, TEST_GUILD_ID)
    @app_commands.describe(user="Optional member to view instead of yourself.")
    async def leaderboard(
        self,
        interaction: discord.Interaction,
        user: Optional[discord.Member] = None,
    ) -> None:
        if interaction.guild is None:
            await interaction.response.send_message(
                **error_card("This command can only be used in a server.").send_kwargs(),
                ephemeral=True,
            )
            return

        target_user: discord.Member
        if user is not None:
            target_user = user
        elif isinstance(interaction.user, discord.Member):
            target_user = interaction.user
        else:
            member = interaction.guild.get_member(interaction.user.id)
            if member is None:
                await interaction.response.send_message(
                    **error_card("Rob could not resolve that member in this server.").send_kwargs(),
                    ephemeral=True,
                )
                return
            target_user = member

        include_test_sends = self.bot.settings.throne_parse_test_sends_as_real_sends
        usernames = self.bot.settings.throne_test_gifter_usernames
        owner_test_user_id = self.bot.settings.throne_test_send_leaderboard_owner_user_id
        guild_id = interaction.guild.id
        user_id = target_user.id
        settings = await self.bot.guild_settings_repo.get(guild_id)

        # Test guild only: viewing requires the leaderboard access role; staff
        # bypass. Elsewhere, or with no role configured, viewing stays open.
        if is_new_system_guild(guild_id):
            view_role_id = (
                getattr(settings, "leaderboard_view_role_id", None)
                if settings is not None
                else None
            )
            if view_role_id is not None:
                viewer = interaction.user
                viewer_roles = getattr(viewer, "roles", None) or []
                has_view_role = any(
                    getattr(role, "id", None) == view_role_id for role in viewer_roles
                )
                if not has_view_role and not is_staff_member(viewer, settings):
                    await interaction.response.send_message(
                        **error_permission(PERMISSION_LEADERBOARD_ROLE_MISSING).send_kwargs(),
                        ephemeral=True,
                    )
                    return

        domme_role_id = settings.domme_role_id if settings is not None else None
        sub_role_id = settings.sub_role_id if settings is not None else None
        has_domme_role = domme_role_id is not None and any(role.id == domme_role_id for role in target_user.roles)
        has_sub_role = sub_role_id is not None and any(role.id == sub_role_id for role in target_user.roles)

        domme_stats_data: DommeStatsCardData | None = None
        if has_domme_role:
            stats = await self.bot.leaderboards_repo.get_domme_stats(
                guild_id,
                domme_user_id=user_id,
                include_test_sends=include_test_sends,
                test_gifter_usernames=usernames,
                owner_test_user_id=owner_test_user_id,
            )
            rank = await self.bot.leaderboards_repo.get_domme_rank(
                guild_id,
                domme_user_id=user_id,
                include_test_sends=include_test_sends,
                test_gifter_usernames=usernames,
                owner_test_user_id=owner_test_user_id,
            )
            latest = await self.bot.leaderboards_repo.get_domme_latest_send(
                guild_id,
                domme_user_id=user_id,
                include_test_sends=include_test_sends,
                test_gifter_usernames=usernames,
                owner_test_user_id=owner_test_user_id,
            )
            top_sub = await self.bot.leaderboards_repo.get_domme_top_sending_sub(
                guild_id,
                domme_user_id=user_id,
                include_test_sends=include_test_sends,
                test_gifter_usernames=usernames,
                owner_test_user_id=owner_test_user_id,
            )
            top_sub_label = "User not in server or has not connected account"
            if top_sub is not None and top_sub.user_id is not None:
                top_sub_label = f"<@{top_sub.user_id}>"
            domme_stats_data = DommeStatsCardData(
                display_name=target_user.display_name,
                rank=rank,
                total_cents=stats.total_cents,
                send_count=stats.send_count,
                top_sub_label=top_sub_label,
                latest_item_name=latest.item_name if latest is not None else None,
                latest_amount_cents=latest.amount_cents if latest is not None else None,
                latest_currency=latest.currency if latest is not None else None,
                latest_item_image_url=latest.item_image_url if latest is not None else None,
            )

        sub_stats_data: SubStatsCardData | None = None
        if has_sub_role:
            stats = await self.bot.leaderboards_repo.get_sub_stats(
                guild_id,
                sub_user_id=user_id,
                include_test_sends=include_test_sends,
                test_gifter_usernames=usernames,
                owner_test_user_id=owner_test_user_id,
            )
            latest = await self.bot.leaderboards_repo.get_sub_latest_send(
                guild_id,
                sub_user_id=user_id,
                include_test_sends=include_test_sends,
                test_gifter_usernames=usernames,
                owner_test_user_id=owner_test_user_id,
            )
            top_domme = await self.bot.leaderboards_repo.get_sub_top_domme(
                guild_id,
                sub_user_id=user_id,
                include_test_sends=include_test_sends,
                test_gifter_usernames=usernames,
                owner_test_user_id=owner_test_user_id,
            )

            def _domme_display_name(domme_user_id: int | None) -> str:
                if domme_user_id is None:
                    return "User not in server or has not connected account"
                member = interaction.guild.get_member(domme_user_id)
                if member is not None:
                    return member.display_name
                return "User not in server or has not connected account"

            sub_stats_data = SubStatsCardData(
                display_name=target_user.display_name,
                total_cents=stats.total_cents,
                send_count=stats.send_count,
                top_domme_label=_domme_display_name(top_domme.user_id if top_domme is not None else None),
                latest_item_name=latest.item_name if latest is not None else None,
                latest_amount_cents=latest.amount_cents if latest is not None else None,
                latest_currency=latest.currency if latest is not None else None,
                latest_item_image_url=latest.item_image_url if latest is not None else None,
                latest_domme_label=_domme_display_name(latest.domme_user_id if latest is not None else None),
            )

        rendered = leaderboard_personal_stats_card(
            domme_stats=domme_stats_data,
            sub_stats=sub_stats_data,
            unregistered_text=(
                "Rob could not find Dom/me or Sub roles on that member yet.\n\n"
                "Ask a moderator to apply the Dom/me or Sub role, then run this command again."
            ),
        )
        await interaction.response.send_message(**rendered.send_kwargs(), ephemeral=False)
