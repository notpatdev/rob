"""Public, compact Components V2 profile rendering and viewer-safe link controls."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, cast

import discord

from bill.components.profile import ORIENTATION_LABELS, profile_wizard_view
from bill.embeds import format_minor_amount
from bill.worker_client import (
    DraftScope,
    LinkType,
    PublicProfile,
    ServerProfileMode,
    WorkerAPIError,
)

if TYPE_CHECKING:
    from bill.bot import BillBot


def _escape(value: str, limit: int = 300) -> str:
    return discord.utils.escape_mentions(discord.utils.escape_markdown(value))[:limit]


def public_profile_view(
    profile: PublicProfile,
    *,
    guild_id: int | str,
    owner_view: bool,
    display_name: str | None = None,
) -> discord.ui.LayoutView:
    """Render public data only; webhook identifiers and URLs never enter this view."""
    view = discord.ui.LayoutView(timeout=None)
    container = discord.ui.Container(accent_color=discord.Color.blurple())
    title = f"{ORIENTATION_LABELS[profile.orientation]} profile"
    if display_name:
        title = f"{_escape(display_name, 80)} — {title}"
    container.add_item(discord.ui.TextDisplay(f"## {title}"))
    identity = ", ".join(
        (
            *profile.selections.pronouns,
            *profile.selections.honourifics,
            *profile.selections.submissive_labels,
        )
    )
    if identity:
        container.add_item(discord.ui.TextDisplay(_escape(identity)))
    container.add_item(
        discord.ui.TextDisplay(
            f"**DMs:** {_escape(profile.dm_status.value.replace('_', ' ').title())}"
        )
    )
    if profile.bio:
        container.add_item(discord.ui.TextDisplay(_escape(profile.bio)))
    if profile.aliases:
        aliases = ", ".join(f"@{alias}" for alias in profile.aliases)
        container.add_item(discord.ui.TextDisplay(f"**Aliases:** {_escape(aliases)}"))
    if profile.throne_connected:
        container.add_item(discord.ui.TextDisplay("Throne: Connected"))
    if profile.public_send_stats and profile.send_stats:
        totals = ", ".join(
            f"{format_minor_amount(stat.total_amount_minor, stat.currency)} ({stat.count})"
            for stat in profile.send_stats
        )
        container.add_item(discord.ui.TextDisplay(f"**Public send stats:** {_escape(totals, 500)}"))
    controls: list[discord.ui.Button] = []
    if any(link.link_type is LinkType.PAYMENT for link in profile.links):
        controls.append(
            discord.ui.Button(
                label="Payment Links",
                custom_id=f"bill:links:{guild_id}:{profile.owner_user_id}:payment",
                style=discord.ButtonStyle.primary,
            )
        )
    if any(link.link_type is LinkType.SOCIAL for link in profile.links):
        controls.append(
            discord.ui.Button(
                label="Socials",
                custom_id=f"bill:links:{guild_id}:{profile.owner_user_id}:social",
                style=discord.ButtonStyle.secondary,
            )
        )
    if owner_view:
        controls.append(
            discord.ui.Button(
                label="Edit",
                custom_id=f"bill:edit:{guild_id}:{profile.owner_user_id}",
                style=discord.ButtonStyle.success,
            )
        )
    if controls:
        container.add_item(discord.ui.ActionRow(*controls))
    view.add_item(container)
    return view


class ProfileLinksDynamic(
    discord.ui.DynamicItem[discord.ui.Button],
    template=re.compile(r"bill:links:(?P<guild>\d+):(?P<owner>\d+):(?P<kind>payment|social)$"),
):
    """Resolve links at click time so link visibility changes are never cached in Discord."""

    def __init__(
        self, item: discord.ui.Button, guild_id: str, owner_id: str, kind: LinkType
    ) -> None:
        super().__init__(item)
        self.guild_id, self.owner_id, self.kind = guild_id, owner_id, kind

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction[discord.Client],
        item: discord.ui.Button,
        match: re.Match[str],
        /,
    ) -> ProfileLinksDynamic:
        return cls(item, match["guild"], match["owner"], LinkType(match["kind"]))

    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        bot = cast("BillBot", interaction.client)
        try:
            result = await bot.require_worker().get_profile(
                guild_id=self.guild_id, user_id=self.owner_id
            )
        except WorkerAPIError:
            await interaction.response.send_message(
                "Bill could not load these links right now.", ephemeral=True
            )
            return
        links = (
            ()
            if result.profile is None
            else tuple(link for link in result.profile.links if link.link_type is self.kind)
        )
        if not links:
            await interaction.response.send_message(
                "There are no public links in this section.", ephemeral=True
            )
            return
        view = discord.ui.LayoutView(timeout=180)
        container = discord.ui.Container()
        container.add_item(
            discord.ui.TextDisplay(
                f"## {'Payment Links' if self.kind is LinkType.PAYMENT else 'Socials'}"
            )
        )
        # Discord link buttons require a direct HTTPS URL; all URLs were validated by Worker.
        for link in links[:12]:
            container.add_item(
                discord.ui.ActionRow(
                    discord.ui.Button(label=_escape(link.public_label, 80), url=link.normalized_url)
                )
            )
        view.add_item(container)
        await interaction.response.send_message(view=view, ephemeral=True)


class ProfileEditDynamic(
    discord.ui.DynamicItem[discord.ui.Button],
    template=re.compile(r"bill:edit:(?P<guild>\d+):(?P<owner>\d+)$"),
):
    def __init__(self, item: discord.ui.Button, guild_id: str, owner_id: str) -> None:
        super().__init__(item)
        self.guild_id, self.owner_id = guild_id, owner_id

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction[discord.Client],
        item: discord.ui.Button,
        match: re.Match[str],
        /,
    ) -> ProfileEditDynamic:
        return cls(item, match["guild"], match["owner"])

    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        if str(interaction.user.id) != self.owner_id:
            await interaction.response.send_message(
                "Only the profile owner can edit it.", ephemeral=True
            )
            return
        bot = cast("BillBot", interaction.client)
        try:
            lookup = await bot.require_worker().get_profile(
                guild_id=self.guild_id,
                user_id=interaction.user.id,
            )
            if lookup.profile is None:
                raise WorkerAPIError("Profile is no longer available")
            scope = (
                DraftScope.GLOBAL
                if int(self.guild_id) == bot.settings.home_guild_id
                else DraftScope.SERVER
            )
            started = await bot.require_worker().start_draft(
                owner_user_id=interaction.user.id,
                origin_guild_id=self.guild_id,
                target_scope=scope,
                guild_id=self.guild_id if scope is DraftScope.SERVER else None,
                server_mode=(
                    lookup.profile.mode or ServerProfileMode.INDEPENDENT
                    if scope is DraftScope.SERVER
                    else None
                ),
            )
            dm = await interaction.user.create_dm()
            await dm.send("Your Bill profile editor is private. Your saved draft is below.")
            await dm.send(view=profile_wizard_view(started.draft))
        except discord.Forbidden:
            await interaction.response.send_message(
                "I couldn't DM you. Please enable direct messages from server members, "
                "then try again.",
                ephemeral=True,
            )
            return
        except WorkerAPIError as exc:
            await interaction.response.send_message(
                f"Bill could not open your editor: {exc}",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            "I sent your private profile editor in a DM.",
            ephemeral=True,
        )
