"""Public, compact Components V2 profile rendering and viewer-safe link controls."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, cast
from urllib.parse import urlsplit

import discord

from bill.components.profile import (
    ORIENTATION_LABELS,
    MemberPresentation,
    member_presentation,
    profile_intro_view,
)
from bill.embeds import format_minor_amount
from bill.worker_client import (
    DraftScope,
    LinkType,
    ProfileLink,
    PublicProfile,
    ServerProfileMode,
    WorkerAPIError,
)

if TYPE_CHECKING:
    from bill.bot import BillBot


def _escape(value: str, limit: int = 300) -> str:
    return discord.utils.escape_mentions(discord.utils.escape_markdown(value))[:limit]


def _profile_section(
    presentation: MemberPresentation,
    *metadata: str,
) -> discord.ui.Section | discord.ui.TextDisplay:
    title = f"### {_escape(presentation.display_name, 80)}"
    rows = tuple(discord.ui.TextDisplay(row) for row in metadata)
    if presentation.avatar_url:
        return discord.ui.Section(
            discord.ui.TextDisplay(title),
            *rows,
            accessory=discord.ui.Thumbnail(
                presentation.avatar_url,
                description=f"{_escape(presentation.display_name, 80)}'s avatar",
            ),
        )
    return discord.ui.TextDisplay("\n".join((title, *metadata)))


def _blockquote(label: str, values: str) -> str:
    return "\n".join(f"> **{label}:** {line}" for line in values.splitlines())


def _is_https_url(value: str) -> bool:
    parsed = urlsplit(value)
    return parsed.scheme == "https" and bool(parsed.netloc)


def profile_links_view(
    links: tuple[ProfileLink, ...],
    *,
    kind: LinkType,
    presentation: MemberPresentation,
    profile_color: int | None = None,
) -> discord.ui.LayoutView:
    """Render one viewer-safe link detail surface with compact HTTPS button rows."""
    title = "Payment Links" if kind is LinkType.PAYMENT else "Socials"
    view = discord.ui.LayoutView(timeout=180)
    container = discord.ui.Container(
        accent_color=None if profile_color is None else discord.Color(profile_color)
    )
    container.add_item(discord.ui.TextDisplay(f"-# Bill Profile · {title}"))
    container.add_item(
        _profile_section(
            presentation,
            f"-# {title} shared in this server",
        )
    )
    container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))
    buttons = [
        discord.ui.Button(label=_escape(link.public_label, 80), url=link.normalized_url)
        for link in links[:12]
        if link.link_type is kind and _is_https_url(link.normalized_url)
    ]
    for index in range(0, len(buttons), 5):
        container.add_item(discord.ui.ActionRow(*buttons[index : index + 5]))
    view.add_item(container)
    return view


def public_profile_view(
    profile: PublicProfile,
    *,
    guild_id: int | str,
    owner_view: bool,
    presentation: MemberPresentation,
) -> discord.ui.LayoutView:
    """Render public data only; webhook identifiers and URLs never enter this view."""
    view = discord.ui.LayoutView(timeout=None)
    container = discord.ui.Container(
        accent_color=None if profile.profile_color is None else discord.Color(profile.profile_color)
    )
    container.add_item(discord.ui.TextDisplay("-# Bill Profile"))
    container.add_item(
        _profile_section(
            presentation,
            f"-# Orientation: {_escape(ORIENTATION_LABELS[profile.orientation], 80)}",
            f"-# DMs: {_escape(profile.dm_status.value.replace('_', ' ').title(), 40)}",
        )
    )
    container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))

    identity_rows = []
    for label, values in (
        ("Pronouns", profile.selections.pronouns),
        ("Honourifics", profile.selections.honourifics),
        ("Submissive labels", profile.selections.submissive_labels),
    ):
        if values:
            identity_rows.append(_blockquote(label, _escape(", ".join(values), 300)))
    if identity_rows:
        container.add_item(discord.ui.TextDisplay("\n".join(identity_rows)))
    if profile.bio:
        container.add_item(
            discord.ui.TextDisplay(
                "\n".join(
                    f"> {line}" for line in _escape(profile.bio, 300).splitlines()
                )
            )
        )
    if profile.aliases:
        aliases = ", ".join(profile.aliases)
        container.add_item(discord.ui.TextDisplay(_blockquote("Aliases", _escape(aliases))))
    if profile.throne_connected:
        container.add_item(discord.ui.TextDisplay("> **Throne:** Connected"))
    if profile.public_send_stats and profile.send_stats:
        totals = "\n".join(
            f"> **{_escape(stat.currency.upper(), 10)}:** "
            f"{_escape(format_minor_amount(stat.total_amount_minor, stat.currency), 80)} "
            f"across {stat.count} send{'s' if stat.count != 1 else ''}"
            for stat in profile.send_stats
        )
        container.add_item(discord.ui.TextDisplay(totals))
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
        container.add_item(discord.ui.Separator(spacing=discord.SeparatorSpacing.small))
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
            else tuple(
                link
                for link in result.profile.links
                if link.link_type is self.kind and _is_https_url(link.normalized_url)
            )
        )
        if not links:
            await interaction.response.send_message(
                "There are no public links in this section.", ephemeral=True
            )
            return
        owner = None
        if interaction.guild is not None and str(interaction.guild.id) == self.guild_id:
            owner = interaction.guild.get_member(int(self.owner_id))
        presentation = (
            member_presentation(owner)
            if owner is not None
            else MemberPresentation("Bill member")
        )
        await interaction.response.send_message(
            view=profile_links_view(
                links,
                kind=self.kind,
                presentation=presentation,
                profile_color=result.profile.profile_color,
            ),
            ephemeral=True,
        )


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
            await dm.send(
                "Your Bill profile editor is private. Changes are saved as you go, and "
                "nothing is published until you choose **Publish**.",
                view=profile_intro_view(started.draft),
            )
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
