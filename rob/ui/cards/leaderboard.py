from __future__ import annotations

import time

import discord

from rob.models import LeaderboardEntry, LeaderboardSummary
from rob.services.leaderboard_status import LeaderboardStatus, render_leaderboard_status
from rob.ui.render import RenderedMessage, require_components_v2
from rob.ui.theme import COLOR_LEADERBOARD
from rob.utils.money import format_money_from_cents


_HELPER_TEXT = (
    "-# Dom/mes: To link your Throne, run /register domme in this server.\n"
    "-# Subs: To link a name you use on Throne, run /register sub."
)


def _line(i: int, label: str) -> str:
    return ["🥇", "🥈", "🥉"][i - 1] if i <= 3 else f"#{i}"


def leaderboard_card(
    *,
    title: str,
    entries: list[LeaderboardEntry],
    summary: LeaderboardSummary,
    footer: str | None = None,
    status: LeaderboardStatus | str = LeaderboardStatus.LIVE,
) -> RenderedMessage:
    del title, summary
    require_components_v2()
    view = discord.ui.LayoutView(timeout=1800)

    if not entries:
        entries_text = (
            "No sends have made it onto the board yet.\n\n"
            "Rob is standing here with a clipboard and absolutely nothing to write down."
        )
    else:
        lines: list[str] = []
        for i, entry in enumerate(entries[:10], 1):
            lines.append(
                f"{_line(i, entry.label)} **{entry.label}**\n"
                f"Amount: {format_money_from_cents(entry.total_cents)} | Total Sends: {entry.send_count}"
            )
        entries_text = "\n\n".join(lines)

    children = [
        discord.ui.TextDisplay("## 🏆 Thy Send Leaderboard"),
        discord.ui.Separator(),
        discord.ui.TextDisplay(f"-# {render_leaderboard_status(status)}"),
        discord.ui.Separator(),
        discord.ui.TextDisplay(entries_text),
        discord.ui.Separator(),
        discord.ui.TextDisplay(_HELPER_TEXT),
    ]
    if footer:
        children.extend([discord.ui.Separator(), discord.ui.TextDisplay(f"-# {footer}")])
    view.add_item(discord.ui.Container(*children, accent_color=COLOR_LEADERBOARD))
    return RenderedMessage(view=view)


def leaderboard_stats_card(
    summary: LeaderboardSummary,
    entries: list[LeaderboardEntry],
    *,
    maintenance_enabled: bool = False,
    footer: str | None = None,
) -> RenderedMessage:
    require_components_v2()
    view = discord.ui.LayoutView(timeout=1800)
    if maintenance_enabled:
        stats_text = (
            "Rob is currently under maintenance, so we've paused the send tracker and leaderboard just until he's done.\n\n"
            "Fear not, once the maintenance is over. All untracked sends made during this time will be sent out and the leaderboard will be updated."
        )
    else:
        now = int(time.time())
        stats_text = (
            f"-# Leaderboard last updated: <t:{now}:R> / <t:{now}:f>\n\n"
            f"-# Leaderboard Leader:\n**{entries[0].label if entries else 'Nobody yet'} - {format_money_from_cents(entries[0].total_cents if entries else 0)}**\n\n"
            f"-# Total Dom/mes on Leaderboard:\n**{summary.domme_count}**\n\n"
            f"-# Total Sends Tracked:\n**{summary.send_count}**\n\n"
            f"-# Total Amount Tracked:\n**{format_money_from_cents(summary.total_cents)}**\n\n"
            f"-# Unclaimed Sends:\n**{summary.unclaimed_send_count} sends / {format_money_from_cents(summary.unclaimed_total_cents)}**"
        )

    children = [
        discord.ui.TextDisplay("## 🏆 Thy Send Leaderboard | Stats"),
        discord.ui.Separator(),
        discord.ui.TextDisplay(stats_text),
    ]
    if footer:
        children.extend([discord.ui.Separator(), discord.ui.TextDisplay(f"-# {footer}")])
    view.add_item(discord.ui.Container(*children, accent_color=COLOR_LEADERBOARD))
    return RenderedMessage(view=view)
