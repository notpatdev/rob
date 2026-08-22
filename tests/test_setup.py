from __future__ import annotations

import discord

from bill.cogs.setup import missing_channel_permissions


def test_missing_channel_permissions_lists_actionable_names() -> None:
    permissions = discord.Permissions.none()
    permissions.view_channel = True

    assert missing_channel_permissions(permissions) == (
        "Send Messages",
        "Embed Links",
        "Read Message History",
    )


def test_complete_channel_permissions_passes() -> None:
    permissions = discord.Permissions.none()
    permissions.update(
        view_channel=True,
        send_messages=True,
        embed_links=True,
        read_message_history=True,
    )

    assert missing_channel_permissions(permissions) == ()
