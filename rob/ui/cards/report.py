from __future__ import annotations

import discord

from rob.ui.render import RenderedMessage, require_components_v2
from rob.ui.theme import COLOR_PRIMARY, COLOR_SUCCESS


def report_submitted_card() -> RenderedMessage:
    require_components_v2()
    view = discord.ui.LayoutView(timeout=600)
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay("-# Report"),
            discord.ui.TextDisplay("## Report Sent"),
            discord.ui.Separator(),
            discord.ui.TextDisplay(
                "Thanks - I've sent that through.\n"
                "If this is urgent, please also let a moderator know."
            ),
            accent_color=COLOR_SUCCESS,
        )
    )
    return RenderedMessage(view=view)


def report_staff_card(
    *,
    reporter_mention: str,
    issue_text: str,
    server_label: str,
    submitted_unix: int,
) -> RenderedMessage:
    require_components_v2()
    view = discord.ui.LayoutView(timeout=1800)
    body = (
        "-# Reporter:\n"
        f"**{reporter_mention}**\n\n"
        "-# Issue:\n"
        f"**{issue_text}**\n\n"
        "-# Server:\n"
        f"**{server_label}**\n\n"
        "-# Submitted:\n"
        f"<t:{submitted_unix}:R> / <t:{submitted_unix}:f>"
    )
    view.add_item(
        discord.ui.Container(
            discord.ui.TextDisplay("-# Report"),
            discord.ui.TextDisplay("## Rob Issue Report"),
            discord.ui.Separator(),
            discord.ui.TextDisplay(body),
            accent_color=COLOR_PRIMARY,
        )
    )
    return RenderedMessage(view=view)
