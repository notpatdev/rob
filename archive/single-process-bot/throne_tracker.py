"""Background Throne tracker for Rob the Bot."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import random
import re
import secrets
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

import aiohttp
import discord
from discord.ext import commands, tasks

from bot.config import BotConfig
from bot.database import Database, EventDommeRegistration, ThroneWishlistItem
from bot.event_views import (
    SendNotificationView,
    ThroneRefreshView,
    format_money,
    format_relative_timestamp,
    format_timestamp,
)
from bot.ui.components import (
    action_section,
    make_container,
    separator,
    simple_view,
    text_block,
)
from bot.throne_scraper import (
    fetch_public_wishlist_items,
    fetch_recent_sends_with_status,
    normalize_throne_registration_input,
    normalize_throne_url,
    resolve_creator_info,
)
from bot.utils import has_admin_command_permissions, normalize_sender_name

log = logging.getLogger(__name__)

_FAILURE_THRESHOLD = 5
_SLOW_RETRY_INTERVAL_S = 60 * 60
_PAGE_ENRICHMENT_COOLDOWN_S = 60 * 60
_DISCORD_USER_REF_RE = re.compile(r"^<@!?(\d+)>$")
_EMBED_COLOR_SUCCESS = 5_763_719
_EMBED_COLOR_ERROR = 13_595_942
_EMBED_COLOR_ADMIN = 15_379_208
_DEFAULT_WEBHOOK_BASE_URL = "https://rob.barecoding.com"


@dataclass(frozen=True)
class PollCycleResult:
    ran: bool
    new_sends_found: int


class ThroneWebhookRefreshConfirmView(discord.ui.LayoutView):
    def __init__(
        self,
        *,
        cog: "ThroneTrackerCog",
        requester_id: int,
        creator_id: int,
        discord_user_id: int,
        throne_handle: str,
        throne_creator_id: str,
    ) -> None:
        super().__init__(timeout=180)
        self.cog = cog
        self.requester_id = requester_id
        self.creator_id = creator_id
        self.discord_user_id = discord_user_id
        self.throne_handle = throne_handle
        self.throne_creator_id = throne_creator_id

        yes_button = discord.ui.Button(label="Yes", style=discord.ButtonStyle.primary)
        no_button = discord.ui.Button(label="No", style=discord.ButtonStyle.secondary)
        yes_button.callback = self._confirm
        no_button.callback = self._cancel

        self.add_item(
            make_container(
                "👑 Rob | Throne Admin | Webhook",
                "Webhook Refresh Request!",
                sections=[
                    separator(),
                    text_block(f"**Throne UID**\n`{self.throne_creator_id}`"),
                    text_block(f"**Throne Username**\n{self.throne_handle}"),
                    text_block(f"**Discord User**\n<@{self.discord_user_id}>"),
                    separator(),
                    text_block(
                        "Do you wish to proceed? This will rotate the webhook secret and DM the user "
                        "a new URL. Their old URL will stop working immediately."
                    ),
                    separator(),
                    action_section("Proceed with webhook reset.", yes_button),
                    action_section("Cancel this request.", no_button),
                ],
                accent_color=_EMBED_COLOR_ADMIN,
                footer="Rob can still walk this back by doing nothing.",
            )
        )

    async def _ensure_requester(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.requester_id:
            return True
        await interaction.response.send_message(
            "That button is not for you.", ephemeral=True
        )
        return False

    async def _confirm(self, interaction: discord.Interaction) -> None:
        if not await self._ensure_requester(interaction):
            return

        webhook_secret = secrets.token_urlsafe(32)
        await self.cog.database.reset_throne_creator_webhook(
            creator_id=self.creator_id,
            webhook_secret=webhook_secret,
        )

        webhook_url = self.cog._build_webhook_url(
            self.throne_creator_id, webhook_secret
        )
        dm_failed = False
        try:
            user = self.cog.bot.get_user(
                self.discord_user_id
            ) or await self.cog.bot.fetch_user(self.discord_user_id)
            await user.send(
                view=simple_view(
                    "👑 Rob | Throne | Webhook Reset",
                    (
                        f"New webhook URL:\n`{webhook_url}`\n\n"
                        "Go to Throne → Settings → Integrations → Webhooks. Replace the old URL with this one. "
                        "Click Save Settings. Then click Test Webhook.\n\n"
                        "The old URL no longer works."
                    ),
                    accent_color=_EMBED_COLOR_ADMIN,
                    footer="Rob rotated the key. Please paste carefully.",
                    timeout=600,
                )
            )
        except (discord.Forbidden, discord.HTTPException):
            dm_failed = True

        await interaction.response.edit_message(
            view=simple_view(
                "✅ Rob | Success | Throne Webhook",
                (
                    f"✅ Webhook secret rotated for `{self.throne_handle}` and DM sent."
                    if not dm_failed
                    else f"✅ Webhook secret rotated for `{self.throne_handle}`."
                ),
                accent_color=_EMBED_COLOR_SUCCESS,
                footer="Rob changed the lock and mailed the key.",
                timeout=60,
            )
        )
        if dm_failed:
            await interaction.followup.send(
                (
                    "Could not DM user — here's the URL, send it manually:\n"
                    f"`{webhook_url}`"
                ),
                ephemeral=True,
            )

    async def _cancel(self, interaction: discord.Interaction) -> None:
        if not await self._ensure_requester(interaction):
            return
        await interaction.response.edit_message(
            view=simple_view(
                "👑 Rob | Throne Admin | Webhook",
                "Cancelled.",
                accent_color=_EMBED_COLOR_ADMIN,
                footer="Rob closed the ticket. No changes made.",
                timeout=30,
            )
        )


class ThroneTrackerCog(commands.Cog):
    def __init__(
        self,
        bot: commands.Bot,
        config: BotConfig,
        database: Database,
    ) -> None:
        self.bot = bot
        self.config = config
        self.database = database
        self._http: aiohttp.ClientSession | None = None
        self._failure_counts: dict[int, int] = {}
        self._slow_retry_until: dict[int, float] = {}
        self._page_enrichment_cooldown_until: dict[int, float] = {}
        self._poll_lock = asyncio.Lock()
        self._warned_runtime_targets: set[str] = set()
        self._last_poll_at: str | None = None
        self._last_successful_poll_at: str | None = None
        self._last_manual_refresh_at: str | None = None
        self._last_error: str | None = None
        self.sync_wishlist_snapshots.start()

    def cog_unload(self) -> None:
        if self.poll_throne_pages.is_running():
            self.poll_throne_pages.cancel()
        if self.sync_wishlist_snapshots.is_running():
            self.sync_wishlist_snapshots.cancel()
        if self._http is not None:
            session = self._http
            self._http = None
            asyncio.create_task(session.close())

    async def _get_http(self) -> aiohttp.ClientSession:
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession()
        return self._http

    def _warn_once(self, key: str, message: str, *args: object) -> None:
        if key in self._warned_runtime_targets:
            return
        self._warned_runtime_targets.add(key)
        log.warning(message, *args)

    @tasks.loop(seconds=300)
    async def poll_throne_pages(self) -> None:
        try:
            await self._run_poll_cycle()
        except Exception:  # noqa: BLE001 - keep the loop alive
            self._last_error = "Polling cycle crashed before completion."
            log.exception("Throne polling cycle raised; will retry next interval.")

    @poll_throne_pages.before_loop
    async def _before_poll(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(hours=2)
    async def sync_wishlist_snapshots(self) -> None:
        try:
            synced = await self._run_wishlist_snapshot_sync()
            if synced:
                log.info("Wishlist snapshot sync refreshed %s creator(s).", synced)
        except Exception:  # noqa: BLE001
            log.exception("Wishlist snapshot sync raised; keeping the loop alive.")

    @sync_wishlist_snapshots.before_loop
    async def _before_wishlist_sync(self) -> None:
        await self.bot.wait_until_ready()

    @commands.group(name="throne", invoke_without_command=True)
    async def throne_group(self, ctx: commands.Context[commands.Bot]) -> None:
        await ctx.reply(
            "Rob knows: `!throne refresh` `!throne status` `!throne list` `!throne search <@user|id>` "
            "`!throne webhook refresh <@user|id>` `!throne addsend <@user|id> <amount> [sub]` "
            "`!throne addsub <@user|id> <name>` `!throne adddomme <@user|id> <url>`",
            mention_author=False,
        )

    @throne_group.command(name="refresh")
    async def throne_refresh(self, ctx: commands.Context[commands.Bot]) -> None:
        if not await self._check_admin_context(ctx):
            return

        event_cog = self.bot.get_cog("RobEventCog")
        context = (
            await event_cog.get_runtime_context() if event_cog is not None else None
        )
        await ctx.reply(
            view=self._simple_admin_view(
                "👑 Rob | Throne Admin | Refresh",
                sections=[
                    text_block("Legacy polling is disabled."),
                    separator(),
                    text_block(
                        f"**Tracking mode**\n{self._refresh_mode_label(context)}"
                    ),
                    separator(),
                    text_block(
                        "**What to do now**\n"
                        "Use Throne webhooks and click **Test Webhook** after pasting the URL."
                    ),
                ],
                footer="Webhook-only now. Rob retired the scraping mop.",
                accent_color=_EMBED_COLOR_ADMIN,
            ),
            mention_author=False,
        )

    @throne_group.command(name="status")
    async def throne_status(self, ctx: commands.Context[commands.Bot]) -> None:
        if not await self._check_admin_context(ctx):
            return

        if ctx.guild is None:
            await ctx.reply("Server only.", mention_author=False)
            return

        creators = await self.database.get_throne_creators_for_guild(
            guild_id=str(ctx.guild.id)
        )
        total_count = len(creators)
        webhook_count = sum(1 for creator in creators if creator.webhook_connected_at)
        pending_count = total_count - webhook_count
        tracking_method = "Webhook only"

        latest = await self.database.get_latest_webhook_send_for_guild(
            guild_id=str(ctx.guild.id)
        )
        if latest is None:
            last_send_line = "Never"
            last_send_user_line = "Unknown"
        else:
            user_label = await self._member_display_label(
                ctx.guild, int(latest.discord_user_id)
            )
            last_send_line = format_timestamp(latest.sent_at)
            last_send_user_line = user_label

        await ctx.reply(
            view=self._simple_admin_view(
                "👑 Rob | Throne Admin | Status",
                sections=[
                    text_block(f"**Current Tracking Method**\n{tracking_method}"),
                    separator(),
                    text_block(f"**Registered Users**\n{total_count}"),
                    separator(),
                    text_block(f"**Webhook Connected**\n{webhook_count}"),
                    separator(),
                    text_block(f"**Waiting For First Webhook**\n{pending_count}"),
                    separator(),
                    text_block(
                        "**Last Successful Send Notification**\n"
                        f"Time: {last_send_line}\n"
                        f"User: {last_send_user_line}"
                    ),
                ],
                footer="Rob keeps the books. The books keep Rob employed.",
                accent_color=_EMBED_COLOR_ADMIN,
            ),
            mention_author=False,
        )

    @throne_group.command(name="list")
    async def throne_list(self, ctx: commands.Context[commands.Bot]) -> None:
        if not await self._check_admin_context(ctx):
            return
        if ctx.guild is None:
            await ctx.reply("Server only.", mention_author=False)
            return

        creators = await self.database.get_throne_creators_for_guild(
            guild_id=str(ctx.guild.id)
        )
        total_count = len(creators)
        if not creators:
            await ctx.reply(
                view=self._simple_admin_view(
                    "👑 Rob | Throne Admin | Users",
                    sections=[text_block("No creators are registered yet.")],
                    footer="Total registered: 0",
                    accent_color=_EMBED_COLOR_ADMIN,
                ),
                mention_author=False,
            )
            return

        pages = self._chunked(creators, 8)
        for index, page in enumerate(pages):
            sections: list[discord.ui.Item] = []
            for creator_index, creator in enumerate(page):
                user_label = await self._member_display_label(
                    ctx.guild, int(creator.discord_user_id)
                )
                status = (
                    "🟢 Connected"
                    if creator.webhook_connected_at
                    else "🟡 Waiting for first webhook"
                )
                if creator.last_successful_event_at:
                    status = f"{status} · last event {format_relative_timestamp(creator.last_successful_event_at)}"
                if creator_index:
                    sections.append(separator())
                sections.append(
                    text_block(
                        f"**Nickname:** **{user_label}**\n"
                        f"Throne Username: `{creator.throne_handle}`\n"
                        f"Throne UID: `{creator.throne_creator_id}`\n"
                        f"Webhook Status: {status}"
                    )
                )
            footer = f"Total registered: {total_count}"
            if len(pages) > 1:
                footer = f"{footer} · Page {index + 1}/{len(pages)}"
            view = self._simple_admin_view(
                "👑 Rob | Throne Admin | Users",
                sections=sections,
                footer=footer,
                accent_color=_EMBED_COLOR_ADMIN,
            )
            if index == 0:
                await ctx.reply(view=view, mention_author=False)
            else:
                await ctx.send(view=view)

    @throne_group.command(name="search")
    async def throne_search(
        self, ctx: commands.Context[commands.Bot], user_ref: str
    ) -> None:
        if not await self._check_admin_context(ctx):
            return
        if ctx.guild is None:
            await ctx.reply("Server only.", mention_author=False)
            return

        user_id = self._parse_user_id(user_ref)
        if user_id is None:
            await ctx.reply(
                view=self._simple_admin_view(
                    "⚠️ Rob | Errors | Throne Search",
                    sections=[
                        text_block("No Throne registration found for that user.")
                    ],
                    footer="Rob checked. Nothing filed under that reference.",
                    accent_color=_EMBED_COLOR_ERROR,
                ),
                mention_author=False,
            )
            return

        creator = await self.database.get_throne_creator_by_discord_user(
            guild_id=str(ctx.guild.id),
            discord_user_id=str(user_id),
        )
        if creator is None:
            await ctx.reply(
                view=self._simple_admin_view(
                    "⚠️ Rob | Errors | Throne Search",
                    sections=[
                        text_block("No Throne registration found for that user.")
                    ],
                    footer="Rob checked. Nothing filed under that reference.",
                    accent_color=_EMBED_COLOR_ERROR,
                ),
                mention_author=False,
            )
            return

        latest_send = await self.database.get_latest_webhook_send_for_domme(
            domme_user_id=user_id
        )
        if latest_send is None:
            send_time = "Never"
            send_from = "Unknown"
            send_amount = "Unknown"
        else:
            send_time = format_timestamp(latest_send.sent_at)
            send_from = latest_send.sub_name or "Unknown"
            send_amount = (
                "Unknown"
                if latest_send.is_private
                else format_money(latest_send.amount_usd)
            )

        webhook_status = (
            "🟢 Connected"
            if creator.webhook_connected_at
            else "🟡 Waiting for first webhook"
        )
        connected_at = (
            format_timestamp(creator.webhook_connected_at)
            if creator.webhook_connected_at
            else "Never"
        )
        user_label = await self._member_display_label(ctx.guild, user_id)
        await ctx.reply(
            view=self._simple_admin_view(
                "👑 Rob | Throne Admin | Details",
                sections=[
                    text_block(
                        "**Identity**\n"
                        f"Discord: {user_label}\n"
                        f"Throne Username: `{creator.throne_handle}`\n"
                        f"Throne UID: `{creator.throne_creator_id}`"
                    ),
                    separator(),
                    text_block(
                        "**Last Recorded Send**\n"
                        f"Time: {send_time}\n"
                        f"From: {send_from}\n"
                        f"Amount: {send_amount}"
                    ),
                    separator(),
                    text_block(
                        "**Webhook Status**\n"
                        f"{webhook_status}\n"
                        f"Connected at: {connected_at}"
                    ),
                ],
                footer="Rob found the row. Begrudgingly.",
                accent_color=_EMBED_COLOR_ADMIN,
            ),
            mention_author=False,
        )

    @throne_group.group(name="webhook", invoke_without_command=True)
    async def throne_webhook_group(self, ctx: commands.Context[commands.Bot]) -> None:
        await ctx.reply(
            "Rob knows: `!throne webhook refresh <@user|id>`", mention_author=False
        )

    @throne_webhook_group.command(name="refresh")
    async def throne_webhook_refresh(
        self, ctx: commands.Context[commands.Bot], user_ref: str
    ) -> None:
        if ctx.guild is None:
            await ctx.reply("Server only.", mention_author=False)
            return
        if not await self._check_owner_context(ctx):
            await ctx.reply("Not authorized.", mention_author=False)
            return

        user_id = self._parse_user_id(user_ref)
        if user_id is None:
            await ctx.reply(
                view=self._simple_admin_view(
                    "⚠️ Rob | Errors | Throne Webhook",
                    sections=[
                        text_block("No Throne registration found for that user.")
                    ],
                    footer="Rob cannot rotate a key without a matching row.",
                    accent_color=_EMBED_COLOR_ERROR,
                ),
                mention_author=False,
            )
            return

        creator = await self.database.get_throne_creator_by_discord_user(
            guild_id=str(ctx.guild.id),
            discord_user_id=str(user_id),
        )
        if creator is None:
            await ctx.reply(
                view=self._simple_admin_view(
                    "⚠️ Rob | Errors | Throne Webhook",
                    sections=[
                        text_block("No Throne registration found for that user.")
                    ],
                    footer="Rob cannot rotate a key without a matching row.",
                    accent_color=_EMBED_COLOR_ERROR,
                ),
                mention_author=False,
            )
            return

        await ctx.reply(
            view=ThroneWebhookRefreshConfirmView(
                cog=self,
                requester_id=ctx.author.id,
                creator_id=creator.id,
                discord_user_id=int(creator.discord_user_id),
                throne_handle=creator.throne_handle,
                throne_creator_id=creator.throne_creator_id,
            ),
            mention_author=False,
        )

    @throne_group.command(name="addsend")
    async def throne_addsend(
        self,
        ctx: commands.Context[commands.Bot],
        user_ref: str,
        amount: float,
        *,
        sub: str = "",
    ) -> None:
        if not await self._check_admin_context(ctx):
            return
        if ctx.guild is None:
            await ctx.reply("Server only.", mention_author=False)
            return

        user_id = self._parse_user_id(user_ref)
        if user_id is None:
            await ctx.reply(
                view=self._simple_admin_view(
                    "⚠️ Rob | Errors | Add Send",
                    sections=[text_block("Could not parse that user reference.")],
                    footer="Try a mention or a numeric Discord user ID.",
                    accent_color=_EMBED_COLOR_ERROR,
                ),
                mention_author=False,
            )
            return

        if amount <= 0:
            await ctx.reply(
                view=self._simple_admin_view(
                    "⚠️ Rob | Errors | Add Send",
                    sections=[text_block("Amount must be greater than zero.")],
                    footer="Rob does not log free sends.",
                    accent_color=_EMBED_COLOR_ERROR,
                ),
                mention_author=False,
            )
            return

        event_cog = self.bot.get_cog("RobEventCog")
        context = (
            await event_cog.get_runtime_context() if event_cog is not None else None
        )
        event_key = (
            context.event_key
            if context is not None and context.is_event_active
            else None
        )

        sub_name = sub.strip() or None
        result = await self.record_send(
            domme_user_id=user_id,
            sub_name=sub_name,
            amount_usd=amount,
            item_name="Admin-logged external send",
            item_image_url=None,
            source="manual:admin",
            is_private=False,
            event_key=event_key,
        )
        if result is None:
            await ctx.reply(
                view=self._simple_admin_view(
                    "⚠️ Rob | Errors | Add Send",
                    sections=[
                        text_block("Send could not be recorded (possible duplicate).")
                    ],
                    footer="Rob checked. Nothing filed under that reference.",
                    accent_color=_EMBED_COLOR_ERROR,
                ),
                mention_author=False,
            )
            return

        _, send_public_id = result
        user_label = await self._member_display_label(ctx.guild, user_id)
        await ctx.reply(
            view=self._simple_admin_view(
                "👑 Rob | Throne Admin | Send Added",
                sections=[
                    text_block(
                        f"**Send ID:** `{send_public_id}`\n"
                        f"**Domme:** {user_label}\n"
                        f"**Amount:** {format_money(amount)}\n"
                        f"**Sub:** {sub_name or '*(none)*'}"
                    )
                ],
                footer="Rob logged it. No receipts needed.",
                accent_color=_EMBED_COLOR_SUCCESS,
            ),
            mention_author=False,
        )

    @throne_group.command(name="addsub")
    async def throne_addsub(
        self,
        ctx: commands.Context[commands.Bot],
        user_ref: str,
        *,
        sub_name: str,
    ) -> None:
        if not await self._check_admin_context(ctx):
            return
        if ctx.guild is None:
            await ctx.reply("Server only.", mention_author=False)
            return

        user_id = self._parse_user_id(user_ref)
        if user_id is None:
            await ctx.reply(
                view=self._simple_admin_view(
                    "⚠️ Rob | Errors | Add Sub",
                    sections=[text_block("Could not parse that user reference.")],
                    footer="Try a mention or a numeric Discord user ID.",
                    accent_color=_EMBED_COLOR_ERROR,
                ),
                mention_author=False,
            )
            return

        clean_name = " ".join(sub_name.strip().split())
        if not clean_name:
            await ctx.reply(
                view=self._simple_admin_view(
                    "⚠️ Rob | Errors | Add Sub",
                    sections=[text_block("A Throne sending name is required.")],
                    footer="Rob needs a name to track sends against.",
                    accent_color=_EMBED_COLOR_ERROR,
                ),
                mention_author=False,
            )
            return

        existing = await self.database.get_event_sub_by_name(sub_name=clean_name)
        if existing is not None and existing.user_id != user_id:
            await ctx.reply(
                view=self._simple_admin_view(
                    "⚠️ Rob | Errors | Add Sub",
                    sections=[
                        text_block(
                            f"The name `{clean_name}` is already claimed by another user."
                        )
                    ],
                    footer="Rob cannot register a name that is taken.",
                    accent_color=_EMBED_COLOR_ERROR,
                ),
                mention_author=False,
            )
            return

        await self.database.save_event_sub(user_id=user_id, sub_name=clean_name)
        event_cog = self.bot.get_cog("RobEventCog")
        if event_cog is not None:
            await event_cog.sync_leaderboard_channel()

        user_label = await self._member_display_label(ctx.guild, user_id)
        await ctx.reply(
            view=self._simple_admin_view(
                "👑 Rob | Throne Admin | Sub Added",
                sections=[
                    text_block(
                        f"**Discord:** {user_label}\n**Sending name:** `{clean_name}`"
                    )
                ],
                footer="Rob linked the sub. Sends will be claimed automatically.",
                accent_color=_EMBED_COLOR_SUCCESS,
            ),
            mention_author=False,
        )

    @throne_group.command(name="adddomme")
    async def throne_adddomme(
        self,
        ctx: commands.Context[commands.Bot],
        user_ref: str,
        throne_url: str,
    ) -> None:
        if not await self._check_admin_context(ctx):
            return
        if ctx.guild is None:
            await ctx.reply("Server only.", mention_author=False)
            return

        user_id = self._parse_user_id(user_ref)
        if user_id is None:
            await ctx.reply(
                view=self._simple_admin_view(
                    "⚠️ Rob | Errors | Add Domme",
                    sections=[text_block("Could not parse that user reference.")],
                    footer="Try a mention or a numeric Discord user ID.",
                    accent_color=_EMBED_COLOR_ERROR,
                ),
                mention_author=False,
            )
            return

        normalized = normalize_throne_registration_input(throne_url.strip())
        if normalized is None:
            await ctx.reply(
                view=self._simple_admin_view(
                    "⚠️ Rob | Errors | Add Domme",
                    sections=[
                        text_block(
                            "That Throne link looks wrong. Try a full Throne URL or username."
                        )
                    ],
                    footer="Rob squinted at that link and found nothing.",
                    accent_color=_EMBED_COLOR_ERROR,
                ),
                mention_author=False,
            )
            return

        http = await self._get_http()
        creator_info = await resolve_creator_info(
            normalized,
            http=http,
            timeout_seconds=self.config.throne_http_timeout_seconds,
        )
        if creator_info is None:
            await ctx.reply(
                view=self._simple_admin_view(
                    "⚠️ Rob | Errors | Add Domme",
                    sections=[
                        text_block(
                            "Rob could not resolve that Throne profile. Check the link and try again."
                        )
                    ],
                    footer="Throne returned nothing useful.",
                    accent_color=_EMBED_COLOR_ERROR,
                ),
                mention_author=False,
            )
            return

        guild_id = str(ctx.guild.id)
        discord_user_id = str(user_id)

        existing = await self.database.get_throne_creator_by_handle(
            guild_id=guild_id,
            throne_handle=creator_info.throne_handle,
        )
        webhook_secret = (
            existing.webhook_secret
            if existing and existing.webhook_secret
            else secrets.token_urlsafe(32)
        )
        tracking_mode = (
            "webhook"
            if existing is not None and existing.tracking_mode == "webhook"
            else "disabled"
        )

        throne_creator = await self.database.upsert_throne_creator(
            guild_id=guild_id,
            discord_user_id=discord_user_id,
            throne_handle=creator_info.throne_handle,
            throne_creator_id=creator_info.creator_id,
            hide_own_purchases=creator_info.hide_own_purchases,
            tracking_mode=tracking_mode,
            webhook_secret=webhook_secret,
            overlay_detected=False,
        )

        await self.database.save_event_domme(user_id=user_id, throne_url=normalized)
        event_cog = self.bot.get_cog("RobEventCog")
        if event_cog is not None:
            await event_cog.sync_leaderboard_channel()

        webhook_url = self._build_webhook_url(
            creator_info.creator_id, throne_creator.webhook_secret
        )
        mode_labels = {
            "webhook": "🟢 Webhook connected",
            "disabled": "🟡 Waiting for webhook setup",
        }
        mode_label = mode_labels.get(tracking_mode, tracking_mode)
        user_label = await self._member_display_label(ctx.guild, user_id)

        sections: list[discord.ui.Item] = [
            text_block(
                f"**Discord:** {user_label}\n"
                f"**Throne Username:** `{creator_info.throne_handle}`\n"
                f"**Throne UID:** `{creator_info.creator_id}`\n"
                f"**Tracking mode:** {mode_label}"
            )
        ]
        if tracking_mode == "disabled":
            sections.append(separator())
            sections.append(
                text_block(
                    "**Webhook URL**\n"
                    "Go to Throne → Settings → Integrations → Webhooks, paste this URL, "
                    "and click Test Webhook:\n"
                    f"`{webhook_url}`"
                )
            )

        await ctx.reply(
            view=self._simple_admin_view(
                "👑 Rob | Throne Admin | Domme Added",
                sections=sections,
                footer="Rob filed the domme. She's on the board.",
                accent_color=_EMBED_COLOR_SUCCESS,
            ),
            mention_author=False,
        )

        if ctx.guild is None or not isinstance(ctx.author, discord.Member):
            await ctx.reply("Server only.", mention_author=False)
            return False
        if not has_admin_command_permissions(ctx.author, self.config):
            await ctx.reply("Nope. Not for you.", mention_author=False)
            return False
        return True

    async def _check_owner_context(self, ctx: commands.Context[commands.Bot]) -> bool:
        owner_ids: set[int] = set()
        raw_owner_ids = os.getenv("BOT_OWNER_ID", "").strip()
        if raw_owner_ids:
            for raw in raw_owner_ids.split(","):
                raw = raw.strip()
                if raw.isdigit():
                    owner_ids.add(int(raw))
        try:
            app_info = await self.bot.application_info()
            if app_info.owner is not None:
                owner_ids.add(app_info.owner.id)
        except discord.HTTPException:
            log.warning(
                "Could not resolve bot application owner for webhook refresh checks.",
                exc_info=True,
            )
        return ctx.author.id in owner_ids

    @staticmethod
    def _parse_user_id(raw: str) -> int | None:
        cleaned = raw.strip()
        if cleaned.isdigit():
            return int(cleaned)
        match = _DISCORD_USER_REF_RE.match(cleaned)
        if match:
            return int(match.group(1))
        return None

    async def _member_display_label(self, guild: discord.Guild, user_id: int) -> str:
        member = guild.get_member(user_id)
        if member is not None:
            return f"<@{user_id}> ({member.display_name})"
        return f"<@{user_id}>"

    def _simple_admin_view(
        self,
        title: str,
        *,
        sections: list[discord.ui.Item],
        footer: str,
        accent_color: int,
    ) -> discord.ui.LayoutView:
        view = discord.ui.LayoutView(timeout=120)
        view.add_item(
            make_container(
                title,
                sections=sections,
                footer=footer,
                accent_color=accent_color,
            )
        )
        return view

    @staticmethod
    def _chunked(items: Sequence[object], size: int) -> list[list[object]]:
        return [
            list(items[index : index + size]) for index in range(0, len(items), size)
        ]

    def _build_webhook_url(self, creator_id: str, secret: str) -> str:
        base_url = (
            self.config.throne_webhook_base_url or _DEFAULT_WEBHOOK_BASE_URL
        ).rstrip("/")
        return f"{base_url}/throne/webhook/{creator_id}/{secret}"

    async def run_manual_refresh(self) -> PollCycleResult:
        if self._poll_lock.locked():
            return PollCycleResult(ran=False, new_sends_found=0)
        new_sends = await self._run_poll_cycle()
        self._last_manual_refresh_at = datetime.now(timezone.utc).isoformat()
        return PollCycleResult(ran=True, new_sends_found=new_sends)

    async def _run_poll_cycle(self, *, force_domme_user_id: int | None = None) -> int:
        async with self._poll_lock:
            self._last_poll_at = datetime.now(timezone.utc).isoformat()
            cycle_error: str | None = None

            event_cog = self.bot.get_cog("RobEventCog")
            context = (
                await event_cog.get_runtime_context() if event_cog is not None else None
            )
            active_event_key = (
                context.event_key
                if context is not None and context.is_event_active
                else None
            )

            profiles = await self.database.get_all_event_dommes()
            tracked = [
                profile
                for profile in profiles
                if profile.throne_url
                and normalize_throne_url(profile.throne_url) is not None
            ]
            if force_domme_user_id is not None:
                tracked = [
                    profile
                    for profile in tracked
                    if profile.user_id == force_domme_user_id
                ]

            if not tracked:
                self._last_successful_poll_at = datetime.now(timezone.utc).isoformat()
                self._last_error = None
                return 0

            if force_domme_user_id is None:
                random.shuffle(tracked)

            posted_total = 0
            for index, profile in enumerate(tracked):
                if force_domme_user_id is None and self._is_in_slow_retry(
                    profile.user_id
                ):
                    continue
                try:
                    posted_total += await self._poll_one_domme(
                        profile, event_key=active_event_key
                    )
                except Exception as exc:  # noqa: BLE001
                    cycle_error = f"Domme {profile.user_id} poll failed: {exc}"
                    log.exception(
                        "Unexpected error polling Domme %s; continuing.",
                        profile.user_id,
                    )
                if index < len(tracked) - 1:
                    delay = self.config.throne_poll_per_domme_delay_seconds
                    if delay > 0:
                        await asyncio.sleep(delay + random.uniform(0, delay / 2))

            if posted_total > 0 and event_cog is not None:
                try:
                    await event_cog.sync_leaderboard_channel(context=context)
                except Exception:  # noqa: BLE001
                    cycle_error = "Leaderboard sync failed after polling."
                    log.exception(
                        "Failed to sync leaderboard channel after Throne poll."
                    )

            self._last_successful_poll_at = datetime.now(timezone.utc).isoformat()
            self._last_error = cycle_error
            return posted_total

    def _is_in_slow_retry(self, domme_user_id: int) -> bool:
        until = self._slow_retry_until.get(domme_user_id)
        if until is None:
            return False
        if time.monotonic() >= until:
            self._slow_retry_until.pop(domme_user_id, None)
            return False
        return True

    def _is_page_enrichment_on_cooldown(self, domme_user_id: int) -> bool:
        until = self._page_enrichment_cooldown_until.get(domme_user_id)
        if until is None:
            return False
        if time.monotonic() >= until:
            self._page_enrichment_cooldown_until.pop(domme_user_id, None)
            return False
        return True

    def _record_failure(self, domme_user_id: int) -> None:
        count = self._failure_counts.get(domme_user_id, 0) + 1
        self._failure_counts[domme_user_id] = count
        if count == _FAILURE_THRESHOLD:
            self._slow_retry_until[domme_user_id] = (
                time.monotonic() + _SLOW_RETRY_INTERVAL_S
            )
            log.warning(
                "Throne scraping for Domme %s failed %s times in a row; backing off to 1-hour retry.",
                domme_user_id,
                count,
            )

    def _record_success(self, domme_user_id: int) -> None:
        self._failure_counts.pop(domme_user_id, None)
        self._slow_retry_until.pop(domme_user_id, None)

    def _start_page_enrichment_cooldown(self, domme_user_id: int) -> None:
        if self._is_page_enrichment_on_cooldown(domme_user_id):
            return
        self._page_enrichment_cooldown_until[domme_user_id] = (
            time.monotonic() + _PAGE_ENRICHMENT_COOLDOWN_S
        )
        log.warning(
            "Throne page enrichment for Domme %s hit HTTP 429. Pausing page enrichment for 60 minutes while overlay tracking keeps running.",
            domme_user_id,
        )

    async def _poll_one_domme(
        self, profile: EventDommeRegistration, *, event_key: str | None
    ) -> int:
        assert profile.throne_url is not None
        http = await self._get_http()
        result = await fetch_recent_sends_with_status(
            profile.throne_url,
            http=http,
            user_agent=self.config.throne_user_agent,
            timeout_seconds=self.config.throne_http_timeout_seconds,
            allow_page_enrichment=not self._is_page_enrichment_on_cooldown(
                profile.user_id
            ),
        )

        if result.page_status == "rate_limited":
            self._start_page_enrichment_cooldown(profile.user_id)

        if result.sends is None:
            self._record_failure(profile.user_id)
            return 0

        self._record_success(profile.user_id)
        scraped = result.sends
        if not scraped:
            return 0

        is_first_run = not await self.database.has_any_event_sends_for_domme(
            domme_user_id=profile.user_id
        )
        known_external_ids = await self.database.get_known_event_external_ids_for_domme(
            domme_user_id=profile.user_id
        )
        new_items = [
            item for item in scraped if item.external_id not in known_external_ids
        ]
        if not new_items:
            return 0

        posted = 0
        for item in new_items:
            send_id = await self.database.log_event_send(
                domme_user_id=profile.user_id,
                sub_name=normalize_sender_name(item.sender_name),
                amount_usd=item.amount_usd if item.amount_usd is not None else 0.0,
                item_name=item.item_name,
                item_image_url=item.item_image_url,
                logged_by=self.bot.user.id if self.bot.user else 0,
                external_id=item.external_id,
                is_private=item.amount_usd is None,
                seeded=is_first_run,
                sent_at=item.sent_at,
                event_key=event_key,
            )
            if send_id is None or is_first_run:
                continue
            await self._post_send_card(profile.user_id, send_id)
            posted += 1
        return posted

    async def record_send(
        self,
        *,
        domme_user_id: int,
        sub_name: str | None,
        amount_usd: float,
        item_name: str | None,
        item_image_url: str | None,
        source: str | None,
        is_private: bool = False,
        sent_at: str | None = None,
        event_key: str | None = None,
        external_id: str | None = None,
        event_id: str | None = None,
        fallback_event_hash: str | None = None,
        seeded: bool = False,
    ) -> tuple[int, str] | None:
        """Insert a send row and run the standard post-send pipeline.

        Returns `(send_id, send_public_id)` on success, or `None` for duplicate/
        rejected inserts (e.g., unique key conflicts).
        """
        send_id = await self.database.log_event_send(
            domme_user_id=domme_user_id,
            sub_name=sub_name,
            amount_usd=amount_usd,
            item_name=item_name,
            item_image_url=item_image_url,
            logged_by=self.bot.user.id if self.bot.user else 0,
            external_id=external_id,
            event_id=event_id,
            fallback_event_hash=fallback_event_hash,
            source=source,
            is_private=is_private,
            seeded=seeded,
            sent_at=sent_at,
            event_key=event_key,
        )
        if send_id is None:
            return None

        send = await self.database.get_event_send(send_id=send_id)
        if send is None:
            return None

        send_public_id = self._public_send_id(
            send_id=send.id,
            domme_user_id=domme_user_id,
            sent_at=send.sent_at,
        )

        try:
            await self._post_send_card(domme_user_id, send_id)
        except Exception:
            log.exception("Failed to post send card for send id %s.", send_id)

        try:
            event_cog = self.bot.get_cog("RobEventCog")
            if event_cog is not None:
                await event_cog.sync_leaderboard_channel()
                await event_cog.process_count_restore_from_send(
                    domme_user_id=domme_user_id, send=send
                )
        except Exception:
            log.exception("Failed to sync leaderboard after send id %s.", send_id)

        return send_id, send_public_id

    async def _post_send_card(self, domme_user_id: int, send_id: int) -> None:
        if not self.config.send_track_channel_id:
            return
        guild = self.bot.get_guild(self.config.guild_id)
        if guild is None:
            return
        channel = guild.get_channel(self.config.send_track_channel_id)
        if channel is None:
            try:
                channel = await guild.fetch_channel(self.config.send_track_channel_id)
            except (discord.NotFound, discord.HTTPException):
                channel = None
        if not isinstance(channel, discord.TextChannel):
            self._warn_once(
                "send_track_channel_runtime",
                "Send-track channel %s is not a text channel; cannot post send cards.",
                self.config.send_track_channel_id,
            )
            return

        send = await self.database.get_event_send(send_id=send_id)
        if send is None:
            return

        domme: discord.Member | discord.User | None = guild.get_member(domme_user_id)
        if domme is None:
            try:
                domme = await self.bot.fetch_user(domme_user_id)
            except (discord.NotFound, discord.HTTPException):
                domme = None

        sub_member: discord.Member | discord.User | None = None
        if send.claimed_sub_user_id is not None:
            sub_member = guild.get_member(send.claimed_sub_user_id)
            if sub_member is None:
                try:
                    sub_member = await self.bot.fetch_user(send.claimed_sub_user_id)
                except (discord.NotFound, discord.HTTPException):
                    sub_member = None

        event_cog = self.bot.get_cog("RobEventCog")
        theme = event_cog.events_config.default_theme if event_cog is not None else None
        if event_cog is not None and send.event_key:
            configured_event = event_cog.events_config.get_event(send.event_key)
            if configured_event is not None:
                theme = configured_event.theme
        elif event_cog is not None:
            theme = event_cog.events_config.default_theme

        domme_label = domme.mention if domme is not None else f"<@{domme_user_id}>"
        domme_nickname = self._member_nickname(domme, fallback="Unknown Domme")
        sender_name = (send.sub_name or "").strip()
        sender_name_cf = sender_name.casefold()
        sender_is_anonymous = not sender_name
        sender_is_test_name = sender_name_cf in {"marie_123", "rob test send"}

        if sender_is_anonymous:
            sub_label = "*👻 The Flying Dutchman*"
        elif sender_is_test_name:
            sub_label = "Rob's Testy Westy Notification"
        elif send.claimed_sub_user_id is None or sub_member is None:
            sub_label = "*Sub with no nickname claimed*"
        else:
            sub_label = sub_member.mention

        domme_rank = await self.database.get_event_domme_rank(
            user_id=domme_user_id,
            event_key=send.event_key,
        )
        domme_rank_value = domme_rank if domme_rank is not None else "?"
        domme_rank_line = f"{domme_label}'s rank after this send #{domme_rank_value}"

        sub_rank_line: str | None = None
        sub_total_line: str | None = None
        anonymous_rank_line: str | None = None
        if send.claimed_sub_user_id is not None and sub_member is not None:
            sub_rank = await self.database.get_event_sub_rank(
                user_id=send.claimed_sub_user_id,
                event_key=send.event_key,
            )
            sub_rank_value = sub_rank if sub_rank is not None else "?"
            sub_total = await self.database.get_event_sub_total(
                user_id=send.claimed_sub_user_id,
                event_key=send.event_key,
            )
            sub_rank_line = (
                f"{sub_member.mention}'s rank after this send #{sub_rank_value}"
            )
            sub_total_line = (
                f"Total amount sent by sub to date: {format_money(sub_total.total_usd)} "
                "(United States Dollar)"
            )
        elif sender_is_anonymous:
            dutchman_rank = await self.database.get_event_unclaimed_rank(
                sub_name=None,
                event_key=send.event_key,
            )
            dutchman_rank_value = dutchman_rank if dutchman_rank is not None else "?"
            anonymous_rank_line = f"The Flying Dutchmans Rank: #{dutchman_rank_value}"

        amount_label = (
            format_money(send.amount_usd) if not send.is_private else "Unknown"
        )
        title = f"💸 New Send to {domme_nickname}! 💸"
        accent_color = (
            theme.accent_color if theme is not None else discord.Colour.green()
        )
        footer = f"Please enjoy this send equally | {format_timestamp(send.sent_at)}"
        send_public_id = self._public_send_id(
            send_id=send.id, domme_user_id=domme_user_id, sent_at=send.sent_at
        )

        if getattr(self.bot, "maintenance_mode", False):
            log.info(
                "Skipping send notification post for send id %s while bot is in maintenance mode.",
                send_id,
            )
            return

        try:
            await channel.send(
                view=SendNotificationView(
                    title=title,
                    accent_color=accent_color,
                    sub_label=sub_label,
                    amount_label=amount_label,
                    item_name=send.item_name,
                    item_image_url=send.item_image_url,
                    send_public_id=send_public_id,
                    domme_rank_line=domme_rank_line,
                    sub_rank_line=sub_rank_line,
                    sub_total_line=sub_total_line,
                    anonymous_rank_line=anonymous_rank_line,
                    footer=footer,
                )
            )
        except discord.HTTPException:
            self._last_error = (
                f"Failed to post send notification for send id {send_id}."
            )
            log.exception(
                "Failed to post send notification for send id %s in channel %s.",
                send_id,
                self.config.send_track_channel_id,
            )

    @staticmethod
    def _member_nickname(
        member: discord.Member | discord.User | None,
        *,
        fallback: str,
    ) -> str:
        if member is None:
            return fallback
        display_name = getattr(member, "display_name", None)
        if isinstance(display_name, str) and display_name.strip():
            return display_name
        name = getattr(member, "name", None)
        if isinstance(name, str) and name.strip():
            return name
        return fallback

    @staticmethod
    def _public_send_id(*, send_id: int, domme_user_id: int, sent_at: str) -> str:
        seed = f"{send_id}:{domme_user_id}:{sent_at}".encode("utf-8")
        digest = hashlib.blake2s(seed, digest_size=4).hexdigest().upper()
        return f"ROB-{send_id:06d}-{digest}"

    def slow_retry_count(self) -> int:
        active = [
            domme_id
            for domme_id in list(self._slow_retry_until)
            if self._is_in_slow_retry(domme_id)
        ]
        return len(active)

    def page_enrichment_cooldown_count(self) -> int:
        active = [
            domme_id
            for domme_id in list(self._page_enrichment_cooldown_until)
            if self._is_page_enrichment_on_cooldown(domme_id)
        ]
        return len(active)

    def _format_iso_label(self, value: str | None) -> str:
        return format_timestamp(value)

    @staticmethod
    def _refresh_mode_label(context: object | None) -> str:
        if context is not None and getattr(context, "is_event_active", False):
            active_event = getattr(context, "active_event", None)
            if active_event is not None:
                return f"{active_event.name} — webhook only"
        return "Webhook only"

    @staticmethod
    def _current_event_label(context: object | None) -> str:
        if context is not None and getattr(context, "is_event_active", False):
            active_event = getattr(context, "active_event", None)
            if active_event is not None:
                return f"{active_event.name} — active"
        return "Not active. Live mode."

    async def _run_wishlist_snapshot_sync(self) -> int:
        if not self.config.guild_id:
            return 0

        creators = await self.database.get_throne_creators_for_guild(
            guild_id=str(self.config.guild_id)
        )
        if not creators:
            return 0

        http = await self._get_http()
        now_str = datetime.now(timezone.utc).isoformat()
        synced = 0
        for creator in creators:
            items = await fetch_public_wishlist_items(
                creator.throne_creator_id,
                http=http,
                timeout_seconds=self.config.throne_http_timeout_seconds,
            )
            if items is None:
                continue
            await self.database.replace_throne_wishlist_items(
                creator_id=creator.throne_creator_id,
                items=[
                    ThroneWishlistItem(
                        creator_id=creator.throne_creator_id,
                        wishlist_item_id=item.wishlist_item_id,
                        item_name=item.item_name,
                        item_image_url=item.item_image_url,
                        amount_usd=item.amount_usd,
                        currency=item.currency,
                        is_available=item.is_available,
                        last_seen_at=now_str,
                    )
                    for item in items
                ],
            )
            synced += 1
        return synced
