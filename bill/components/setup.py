"""Public, initiator-bound Components V2 renderer for ``/bill setup``."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, cast

import discord
from discord import app_commands

from bill.components.custom_ids import (
    decode_resource_id,
    decode_uint,
    encode_resource_id,
    encode_uint,
)
from bill.worker_client import GuildSetupSession, WorkerAPIError

if TYPE_CHECKING:
    from bill.bot import BillBot


def missing_channel_permissions(permissions: discord.Permissions) -> tuple[str, ...]:
    required = {
        "view_channel": "View Channel",
        "send_messages": "Send Messages",
        "embed_links": "Embed Links",
        "read_message_history": "Read Message History",
    }
    return tuple(label for name, label in required.items() if not getattr(permissions, name))


async def _resolve_selected_text_channel(
    selected: app_commands.AppCommandChannel | app_commands.AppCommandThread,
    *,
    guild: discord.Guild,
    client: discord.Client,
) -> discord.TextChannel | None:
    if (
        not isinstance(selected, app_commands.AppCommandChannel)
        or selected.guild_id != guild.id
        or selected.type is not discord.ChannelType.text
    ):
        return None

    channel = guild.get_channel(selected.id)
    if channel is None:
        channel = await client.fetch_channel(selected.id)

    if (
        not isinstance(channel, discord.TextChannel)
        or channel.guild.id != guild.id
        or channel.type is not discord.ChannelType.text
    ):
        return None
    return channel


def setup_custom_id(session: GuildSetupSession, action: str) -> str:
    custom_id = (
        f"bill:s:{encode_resource_id(session.id)}:{encode_uint(session.initiator_user_id)}:"
        f"{encode_uint(session.guild_id)}:{encode_uint(session.revision)}:{action}"
    )
    if len(custom_id) > 100:
        raise ValueError("Bill setup component ID exceeds Discord's 100-character limit")
    return custom_id


def setup_view(session: GuildSetupSession) -> discord.ui.LayoutView:
    view = discord.ui.LayoutView(timeout=None)
    container = discord.ui.Container(accent_color=discord.Color.blurple())
    container.add_item(discord.ui.TextDisplay("## Set up Bill"))
    if session.status == "completed":
        container.add_item(
            discord.ui.TextDisplay(
                f"Bill is configured to post sends in <#{session.selected_channel_id}>."
            )
        )
    elif session.selected_channel_id:
        container.add_item(
            discord.ui.TextDisplay(f"-# Channel: <#{session.selected_channel_id}> (Complete)")
        )
        container.add_item(discord.ui.TextDisplay("### Confirm this channel"))
        container.add_item(
            discord.ui.ActionRow(
                discord.ui.Button(
                    label="Confirm setup",
                    style=discord.ButtonStyle.success,
                    custom_id=setup_custom_id(session, "complete"),
                )
            )
        )
    else:
        container.add_item(discord.ui.TextDisplay("### Choose where Bill posts Throne sends"))
        container.add_item(
            discord.ui.ActionRow(
                discord.ui.ChannelSelect(
                    custom_id=setup_custom_id(session, "channel"),
                    channel_types=[discord.ChannelType.text],
                    placeholder="Select a text channel",
                )
            )
        )
    view.add_item(container)
    return view


async def _authorized_setup(
    interaction: discord.Interaction[discord.Client],
    session: GuildSetupSession,
    initiator: str,
    guild: str,
) -> bool:
    if (
        interaction.guild_id is None
        or str(interaction.guild_id) != guild
        or str(interaction.user.id) != initiator
        or session.guild_id != guild
        or session.initiator_user_id != initiator
    ):
        await interaction.response.send_message(
            "Only the administrator who started this setup can continue it.", ephemeral=True
        )
        return False
    if (
        not isinstance(interaction.user, discord.Member)
        or not interaction.user.guild_permissions.manage_guild
    ):
        await interaction.response.send_message(
            "You need **Manage Server** to configure Bill.", ephemeral=True
        )
        return False
    return True


class SetupChannelDynamic(
    discord.ui.DynamicItem[discord.ui.ChannelSelect],
    template=re.compile(
        r"bill:s:(?P<session>[A-Za-z0-9_-]+):(?P<initiator>[a-z0-9]+):"
        r"(?P<guild>[a-z0-9]+):(?P<revision>[a-z0-9]+):channel$"
    ),
):
    def __init__(
        self,
        item: discord.ui.ChannelSelect,
        session_id: str,
        initiator: str,
        guild: str,
        revision: int,
    ) -> None:
        super().__init__(item)
        self.session_id, self.initiator, self.guild, self.revision = (
            session_id,
            initiator,
            guild,
            revision,
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction[discord.Client],
        item: discord.ui.ChannelSelect,
        match: re.Match[str],
        /,
    ) -> SetupChannelDynamic:
        return cls(
            item,
            decode_resource_id(match["session"]),
            str(decode_uint(match["initiator"])),
            str(decode_uint(match["guild"])),
            decode_uint(match["revision"]),
        )

    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        bot = cast("BillBot", interaction.client)
        try:
            session = await bot.require_worker().get_guild_setup(self.session_id)
        except WorkerAPIError:
            await interaction.response.send_message(
                "That setup session is no longer available.", ephemeral=True
            )
            return
        if not await _authorized_setup(interaction, session, self.initiator, self.guild):
            return
        if session.revision != self.revision or session.status != "active" or not self.item.values:
            await interaction.response.send_message(
                "That setup control is stale. Please use the latest message.", ephemeral=True
            )
            return
        if interaction.guild is None or interaction.guild.me is None:
            await interaction.response.send_message(
                "Please choose a standard text channel.", ephemeral=True
            )
            return
        try:
            channel = await _resolve_selected_text_channel(
                self.item.values[0],
                guild=interaction.guild,
                client=interaction.client,
            )
        except discord.HTTPException:
            await interaction.response.send_message(
                "Bill could not load that channel. Please try again.", ephemeral=True
            )
            return
        if channel is None:
            await interaction.response.send_message(
                "Please choose a standard text channel.", ephemeral=True
            )
            return
        missing = missing_channel_permissions(channel.permissions_for(interaction.guild.me))
        if missing:
            await interaction.response.send_message(
                f"Bill needs {', '.join(f'**{name}**' for name in missing)} in {channel.mention}.",
                ephemeral=True,
            )
            return
        try:
            updated = await bot.require_worker().set_guild_setup_channel(
                self.session_id,
                guild_id=interaction.guild_id,
                initiator_user_id=interaction.user.id,
                expected_revision=self.revision,
                channel_id=channel.id,
            )
        except WorkerAPIError as exc:
            await interaction.response.send_message(
                f"Bill could not save that channel: {exc}", ephemeral=True
            )
            return
        await interaction.response.edit_message(view=setup_view(updated))


class SetupCompleteDynamic(
    discord.ui.DynamicItem[discord.ui.Button],
    template=re.compile(
        r"bill:s:(?P<session>[A-Za-z0-9_-]+):(?P<initiator>[a-z0-9]+):"
        r"(?P<guild>[a-z0-9]+):(?P<revision>[a-z0-9]+):complete$"
    ),
):
    def __init__(
        self,
        item: discord.ui.Button,
        session_id: str,
        initiator: str,
        guild: str,
        revision: int,
    ) -> None:
        super().__init__(item)
        self.session_id, self.initiator, self.guild, self.revision = (
            session_id,
            initiator,
            guild,
            revision,
        )

    @classmethod
    async def from_custom_id(
        cls,
        interaction: discord.Interaction[discord.Client],
        item: discord.ui.Button,
        match: re.Match[str],
        /,
    ) -> SetupCompleteDynamic:
        return cls(
            item,
            decode_resource_id(match["session"]),
            str(decode_uint(match["initiator"])),
            str(decode_uint(match["guild"])),
            decode_uint(match["revision"]),
        )

    async def callback(self, interaction: discord.Interaction[discord.Client]) -> None:
        bot = cast("BillBot", interaction.client)
        try:
            session = await bot.require_worker().get_guild_setup(self.session_id)
        except WorkerAPIError:
            await interaction.response.send_message(
                "That setup session is no longer available.", ephemeral=True
            )
            return
        if not await _authorized_setup(interaction, session, self.initiator, self.guild):
            return
        if (
            session.revision != self.revision
            or session.status != "active"
            or not session.selected_channel_id
        ):
            await interaction.response.send_message(
                "That setup control is stale. Please use the latest message.", ephemeral=True
            )
            return
        channel = (
            interaction.guild.get_channel(int(session.selected_channel_id))
            if interaction.guild
            else None
        )
        if (
            not isinstance(channel, discord.TextChannel)
            or interaction.guild is None
            or interaction.guild.me is None
        ):
            await interaction.response.send_message(
                "The selected channel is no longer available.", ephemeral=True
            )
            return
        missing = missing_channel_permissions(channel.permissions_for(interaction.guild.me))
        if missing:
            await interaction.response.send_message(
                f"Bill needs {', '.join(f'**{name}**' for name in missing)} in {channel.mention}.",
                ephemeral=True,
            )
            return
        try:
            completed = await bot.require_worker().complete_guild_setup(
                self.session_id,
                guild_id=interaction.guild_id,
                initiator_user_id=interaction.user.id,
                expected_revision=self.revision,
            )
        except WorkerAPIError as exc:
            await interaction.response.send_message(
                f"Bill could not complete setup: {exc}", ephemeral=True
            )
            return
        await interaction.response.edit_message(view=setup_view(completed.session))
