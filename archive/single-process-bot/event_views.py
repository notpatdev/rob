from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import discord

from bot.ui.cards import success_view
from bot.ui.components import (
    action_section,
    make_container,
    separator,
    subtle,
    text_block,
    thumbnail_section,
)
from bot.ui.copy import DEPLOY_NOTIFICATION

if TYPE_CHECKING:
    from bot.event_cog import RobEventCog


log = logging.getLogger(__name__)


_COLOR_INFO = 9_133_302
_COLOR_SUCCESS = 5_763_719
_COLOR_ADMIN = 15_379_208


def _medal(rank: int) -> str:
    return {1: "🥇", 2: "🥈", 3: "🥉"}.get(rank, f"**#{rank}**")


def format_money(amount: float | None) -> str:
    if amount is None:
        return "Unknown"
    return f"${amount:,.2f}"


def format_timestamp(value: str | None) -> str:
    if not value:
        return "Never"
    try:
        timestamp = int(datetime.fromisoformat(value).timestamp())
    except ValueError:
        return value
    return f"<t:{timestamp}:R> / <t:{timestamp}:f>"


def format_relative_timestamp(value: str | None) -> str:
    if not value:
        return "Never"
    try:
        timestamp = int(datetime.fromisoformat(value).timestamp())
    except ValueError:
        return value
    return f"<t:{timestamp}:R>"


def _send_suffix(count: int) -> str:
    return "send" if count == 1 else "sends"


def _render_rows(rows: list[tuple[str, float, int]]) -> str:
    lines: list[str] = []
    for index, (label, total, sends) in enumerate(rows, 1):
        lines.append(
            f"> {_medal(index)} **{label}** — {format_money(total)} ({sends} {_send_suffix(sends)})"
        )
    return "\n".join(lines)


def _render_unclaimed_rows(rows: list[tuple[str, float, int]]) -> str:
    lines: list[str] = []
    for label, total, sends in rows:
        lines.append(
            f"> 👻 **{label}** — {format_money(total)} ({sends} {_send_suffix(sends)})"
        )
    return "\n".join(lines)


class DommeSignupModal(discord.ui.Modal, title="Domme Sign-Up"):
    throne_input: discord.ui.TextInput = discord.ui.TextInput(
        label="Throne username or link",
        placeholder="mistressxxx or https://throne.com/mistressxxx",
        min_length=2,
        max_length=200,
    )

    def __init__(self, cog: RobEventCog) -> None:
        super().__init__(timeout=300)
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.process_domme_signup(interaction, self.throne_input.value)


class SubSignupModal(discord.ui.Modal, title="Sub Sign-Up"):
    name_input: discord.ui.TextInput = discord.ui.TextInput(
        label="Your Throne sending name",
        placeholder="The name you use on Throne",
        min_length=1,
        max_length=100,
    )

    def __init__(self, cog: RobEventCog) -> None:
        super().__init__(timeout=300)
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.process_sub_signup(interaction, self.name_input.value)


class LeaderboardView(discord.ui.LayoutView):
    def __init__(
        self,
        *,
        cog: RobEventCog | None = None,
        title: str,
        board_title: str | None,
        status_lines: list[str],
        rows: list[tuple[str, float, int]],
        empty_message: str,
        accent_color: discord.Colour | int,
        register_kind: str | None = None,
        register_button_label: str | None = None,
        register_section_text: str | None = None,
        unclaimed_rows: list[tuple[str, float, int]] | None = None,
        unclaimed_total: str | None = None,
        helper_lines: list[str] | None = None,
        footer: str | None = None,
    ) -> None:
        super().__init__(timeout=None)
        self.cog = cog
        self.register_kind = register_kind

        register_button: discord.ui.Button | None = None
        if cog is not None and register_kind and register_button_label:
            register_button = discord.ui.Button(
                label=register_button_label,
                style=discord.ButtonStyle.primary,
                custom_id=f"leaderboard:register:{register_kind}",
            )
            register_button.callback = self._open_register_modal

        pre_rows_sections, post_rows_sections = self._build_register_sections(
            register_button=register_button,
            register_kind=register_kind,
            register_section_text=register_section_text,
            unclaimed_total=unclaimed_total,
        )

        status_block = "\n".join(status_lines)
        if board_title:
            status_block = f"### {board_title}\n\n{status_block}"

        sections: list[discord.ui.Item] = [
            separator(),
            text_block(status_block),
            *pre_rows_sections,
            separator(),
            text_block(_render_rows(rows) if rows else empty_message),
            *post_rows_sections,
        ]
        if helper_lines:
            sections.extend([separator()])
            sections.extend(subtle(line) for line in helper_lines if line)

        self.add_item(
            make_container(
                title,
                None,
                sections=sections,
                accent_color=accent_color,
            )
        )
        if footer:
            self.add_item(subtle(footer))

    def _build_register_sections(
        self,
        *,
        register_button: discord.ui.Button | None,
        register_kind: str | None,
        register_section_text: str | None,
        unclaimed_total: str | None,
    ) -> tuple[list[discord.ui.Item], list[discord.ui.Item]]:
        if register_button is None or not register_section_text:
            return [], []
        if register_kind == "domme":
            return [
                separator(),
                action_section(register_section_text, register_button),
            ], []
        if register_kind == "sub":
            display_unclaimed_total = (
                unclaimed_total if unclaimed_total is not None else "$0.00"
            )
            return [], [
                separator(),
                action_section(
                    "### Unclaimed Sends\n\n"
                    f"Tracked and waiting to be claimed: {display_unclaimed_total}\n\n"
                    f"{register_section_text}",
                    register_button,
                ),
            ]
        raise ValueError(
            f"Unsupported leaderboard registration type: {register_kind!r}. Expected 'domme' or 'sub'."
        )

    async def _open_register_modal(self, interaction: discord.Interaction) -> None:
        if self.cog is None or self.register_kind is None:
            log.error(
                "Leaderboard register button invoked without a configured cog or registration type."
            )
            await interaction.response.send_message(
                "Rob dropped the clipboard. Try again.", ephemeral=True
            )
            return
        if self.register_kind == "domme":
            await interaction.response.send_modal(DommeSignupModal(self.cog))
            return
        if self.register_kind == "sub":
            await interaction.response.send_modal(SubSignupModal(self.cog))
            return
        log.error(
            "Leaderboard register button received unsupported type: %s",
            self.register_kind,
        )
        await interaction.response.send_message(
            "Rob lost track of that button. Try again in a sec.", ephemeral=True
        )


class MaintenanceView(discord.ui.LayoutView):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        body = (
            "## Rob is under maintenance \n\n"
            " Sorry! Rob is undergoing some maintenance to help keep him online and functional. \n\n"
            " During this time, Rob will be unavailable for use and the leaderboard and send tracking will be offline."
            " However fear not, once maintenance is over. the leaderboard and any sends made during this time will be caught back up."
        )
        self.add_item(
            make_container(
                "🛠️ Rob | System Maintenance",
                None,
                sections=[separator(), text_block(body)],
                accent_color=_COLOR_ADMIN,
            )
        )


class OfflineView(discord.ui.LayoutView):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(
            make_container(
                "🛠️ Sub Leaderboard",
                "Offline for maintenance.",
                accent_color=_COLOR_ADMIN,
            )
        )


class BroadcastNotificationView(discord.ui.LayoutView):
    def __init__(
        self,
        *,
        title: str,
        message: str,
        action_url: str | None = None,
    ) -> None:
        super().__init__(timeout=None)
        sections: list[discord.ui.Item] = [separator(), text_block(message)]
        if action_url:
            link_button = discord.ui.Button(
                label="Open Link",
                style=discord.ButtonStyle.link,
                url=action_url,
            )
            sections.append(separator())
            sections.append(action_section("More info / take action:", link_button))
        self.add_item(
            make_container(
                title,
                None,
                sections=sections,
                accent_color=_COLOR_INFO,
            )
        )


class SendNotificationView(discord.ui.LayoutView):
    def __init__(
        self,
        *,
        title: str,
        accent_color: discord.Colour | int,
        sub_label: str,
        amount_label: str,
        item_name: str | None,
        item_image_url: str | None,
        send_public_id: str,
        domme_rank_line: str,
        sub_rank_line: str | None,
        sub_total_line: str | None,
        anonymous_rank_line: str | None,
        footer: str,
    ) -> None:
        super().__init__(timeout=None)
        sections: list[discord.ui.Item] = [separator()]
        sections.append(text_block(f"**Sub:** {sub_label}"))
        sections.append(
            text_block(f"**Amount:** {amount_label} (United States Dollar)")
        )

        item_text = f"**Item:** {item_name or 'Unknown item'}"
        if item_image_url:
            sections.append(thumbnail_section(item_text, item_image_url))
        else:
            sections.append(text_block(item_text))

        sections.append(text_block(f"**Rob Send ID:** `{send_public_id}`"))

        rank_lines = [domme_rank_line]
        if sub_rank_line:
            rank_lines.append(sub_rank_line)
        if sub_total_line:
            rank_lines.append(sub_total_line)
        if anonymous_rank_line:
            rank_lines.append(anonymous_rank_line)
        sections.extend([separator(), text_block("\n".join(rank_lines))])
        sections.append(separator())

        self.add_item(
            make_container(
                title,
                None,
                sections=sections,
                accent_color=accent_color,
                footer=footer,
            )
        )


class EventStatusView(discord.ui.LayoutView):
    def __init__(
        self,
        *,
        source_path: str,
        current_mode: str,
        default_theme_label: str,
        configured_events: list[str],
        domme_count: int,
        sub_count: int,
        live_send_count: int,
        live_send_total: str,
    ) -> None:
        super().__init__(timeout=60)

        event_block = (
            "\n".join(configured_events)
            if configured_events
            else "No event rows loaded."
        )
        self.add_item(
            make_container(
                "🎉 Rob | Events | Status",
                "Events are driven from JSON now.",
                sections=[
                    separator(),
                    text_block(f"**Config source**\n`{source_path}`"),
                    text_block(f"**Current mode**\n{current_mode}"),
                    text_block(f"**Default theme**\n{default_theme_label}"),
                    text_block(f"**Configured events**\n{event_block}"),
                    separator(),
                    text_block(
                        f"**Registrations**\n{domme_count} Dommes · {sub_count} Subs"
                    ),
                    text_block(
                        f"**Live totals**\n{live_send_count} sends · {live_send_total}"
                    ),
                ],
                footer="Rob read the config and complained quietly.",
                accent_color=_COLOR_INFO,
            )
        )


class ThroneRefreshView(discord.ui.LayoutView):
    def __init__(
        self,
        *,
        ran: bool,
        detail: str,
        new_sends_found: int,
        tracking_mode: str,
        slow_retry_count: int,
        page_cooldown_count: int,
    ) -> None:
        super().__init__(timeout=60)

        page_status = (
            f"Paused for **{page_cooldown_count}** profile"
            f"{'' if page_cooldown_count == 1 else 's'}"
            if page_cooldown_count
            else "Live"
        )
        retry_status = (
            f"{slow_retry_count} profile{' is' if slow_retry_count == 1 else 's are'} in slow retry"
            if slow_retry_count
            else "No slow retry backlog."
        )

        self.add_item(
            make_container(
                "👑 Rob | Throne Admin | Refresh",
                detail,
                sections=[
                    separator(),
                    text_block(f"**Refresh**\n{'Ran' if ran else 'Skipped'}"),
                    text_block(f"**New sends found**\n**{new_sends_found}**"),
                    text_block(f"**Tracking mode**\n{tracking_mode}"),
                    text_block(f"**Page enrichment**\n{page_status}"),
                    text_block(f"**Cooldowns**\n{retry_status}"),
                ],
                accent_color=_COLOR_ADMIN,
                footer="One manual cycle. No dramatic overpulling.",
            )
        )


class ThroneStatusView(discord.ui.LayoutView):
    def __init__(
        self,
        *,
        tracking_state: str,
        current_event: str,
        tracked_dommes: int,
        poll_interval_seconds: int,
        per_domme_delay_seconds: float,
        slow_retry_count: int,
        page_cooldown_count: int,
        last_poll_at: str,
        last_successful_poll_at: str,
        last_manual_refresh_at: str,
        last_error: str,
    ) -> None:
        super().__init__(timeout=60)

        self.add_item(
            make_container(
                "👑 Rob | Throne Admin | Tracker",
                None,
                sections=[
                    separator(),
                    text_block(f"**Tracking**\n{tracking_state}"),
                    text_block(f"**Current event**\n{current_event}"),
                    text_block(f"**Registered Dommes**\n{tracked_dommes}"),
                    text_block(f"**Poll interval**\n{poll_interval_seconds} seconds"),
                    text_block(
                        f"**Per-Domme delay**\n{per_domme_delay_seconds:g} seconds"
                    ),
                    text_block(
                        "**Cooldowns**\n"
                        f"{page_cooldown_count} page enrichment cooldown"
                        f"{'' if page_cooldown_count == 1 else 's'}\n"
                        f"{slow_retry_count} profile{' is' if slow_retry_count == 1 else 's are'} in slow retry"
                    ),
                    text_block(f"**Last poll**\n{last_poll_at}"),
                    text_block(f"**Last successful poll**\n{last_successful_poll_at}"),
                    text_block(f"**Last manual refresh**\n{last_manual_refresh_at}"),
                    text_block(f"**Last error**\n{last_error}"),
                ],
                accent_color=_COLOR_ADMIN,
                footer="Powered by vibes and SQLite.",
            )
        )


class FinalReportSummaryView(discord.ui.LayoutView):
    def __init__(
        self,
        *,
        title: str,
        accent_color: discord.Colour | int,
        event_key: str,
        started_at: str,
        ended_at: str,
        total_send_amount: str,
        total_send_count: int,
        dommes_ranked: int,
        subs_ranked: int,
        unclaimed_total: str,
        generated_at: str,
    ) -> None:
        super().__init__(timeout=None)
        self.add_item(
            make_container(
                title,
                None,
                sections=[
                    separator(),
                    text_block(f"**Event key**\n`{event_key}`"),
                    text_block(
                        f"**Event window**\nStarted: {started_at}\nEnded: {ended_at}"
                    ),
                    text_block(
                        "**Summary**\n"
                        f"Total tracked: **{total_send_amount}**\n"
                        f"Sends recorded: **{total_send_count}**\n"
                        f"Dommes ranked: **{dommes_ranked}**\n"
                        f"Subs ranked: **{subs_ranked}**\n"
                        f"Unclaimed: **{unclaimed_total}**"
                    ),
                ],
                accent_color=accent_color,
                footer=f"Generated {generated_at}",
            )
        )


class UpdateNotificationView(discord.ui.LayoutView):
    def __init__(self) -> None:
        super().__init__(timeout=86400)

        now_ts = int(datetime.now(timezone.utc).timestamp())

        ack_btn = discord.ui.Button(
            label="Acknowledge",
            style=discord.ButtonStyle.success,
            emoji="✅",
        )
        ack_btn.callback = self._acknowledge

        self.add_item(
            make_container(
                "✅ Rob | Success | Startup DM",
                (
                    f"Rob came back online <t:{now_ts}:R>.\n"
                    "Latest code should be live now."
                ),
                sections=[
                    separator(),
                    discord.ui.Section(
                        "Dismiss this tiny announcement.", accessory=ack_btn
                    ),
                ],
                accent_color=_COLOR_SUCCESS,
                footer=DEPLOY_NOTIFICATION,
            )
        )

    async def _acknowledge(self, interaction: discord.Interaction) -> None:
        now_ts = int(datetime.now(timezone.utc).timestamp())
        await interaction.response.edit_message(
            view=success_view(
                "Sorted.",
                f"Acknowledged <t:{now_ts}:R>.",
                timeout=1,
            )
        )
