from __future__ import annotations

import ast
import asyncio
import inspect
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import discord

from rob.database.repositories.bot_settings import BotSettingsRepository
from rob.database.repositories.counting import CountingRepository
from rob.database.repositories.dommes import DommesRepository
from rob.database.repositories.guild_settings import GuildSettingsRepository
from rob.models import CountRecoveryWindow
from rob.database.repositories.subs import SubsRepository
from rob.services import wind_down
from rob.services.send_display import is_known_test_sender
from rob.ui.cards.counting import (
    count_failed_reset_card,
    count_failed_sub_blocked_card,
    count_rescue_needed_for_role_card,
    count_saved_card,
)
from rob.ui.emojis import ROBBLANK, ROBNO, ROBYES

log = logging.getLogger(__name__)

_CLAIMED_ROLE_PREFIX_KEY = "count_claimed_role_prefix"
_DEFAULT_CLAIMED_ROLE_PREFIX = "Claimed by "
_CLAIM_UNRESOLVED_SENTINEL = 0

# Special-case sub: this user may only recover the count by sending to one specific domme.
_SPECIAL_SUB_USER_ID = 1299308718009356289
_SPECIAL_SUB_REQUIRED_DOMME_USER_ID = 712738633391800320
_COUNT_HIGH_WATERMARK_KEY_PREFIX = "count_high_watermark:"
_MAX_COUNT_EXPRESSION_LENGTH = 80
_ALLOWED_EXPRESSION_CHARS = set("0123456789+-*/() ")


@dataclass(frozen=True)
class CountingProcessResult:
    success: bool
    expected_number: int
    current_number: int
    reason: str | None = None
    deadline: datetime | None = None
    blocked_until: datetime | None = None
    reactions: tuple[str, ...] = ()


@dataclass
class _WindowRuntime:
    window_id: int
    guild_id: int
    channel_id: int
    message: discord.Message | None = None
    failed_message: discord.Message | None = None


@dataclass(frozen=True)
class _ClaimResolution:
    required_domme_user_id: int | None
    required_domme_id: int | None
    claimed_restriction: bool
    claimed_unresolved: bool


class _ExpressionEvaluator:
    @classmethod
    def evaluate(cls, expression: str) -> int:
        expr = expression.strip()
        if not expr:
            raise ValueError("Expression is empty.")
        if len(expr) > _MAX_COUNT_EXPRESSION_LENGTH:
            raise ValueError("Expression is too long.")
        if any(ch not in _ALLOWED_EXPRESSION_CHARS for ch in expr):
            raise ValueError("Expression contains unsupported characters.")
        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError as exc:
            raise ValueError("Expression syntax is invalid.") from exc
        value = cls._eval_node(tree.body)
        if not isinstance(value, int):
            raise ValueError("Expression did not evaluate to an integer.")
        if value < 0:
            raise ValueError("Negative results are not supported.")
        return value

    @classmethod
    def _eval_node(cls, node: ast.AST) -> int:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, int):
                raise ValueError("Only integer constants are supported.")
            return int(node.value)
        if isinstance(node, ast.UnaryOp):
            operand = cls._eval_node(node.operand)
            if isinstance(node.op, ast.UAdd):
                return operand
            if isinstance(node.op, ast.USub):
                return -operand
            raise ValueError("Unsupported unary operator.")
        if isinstance(node, ast.BinOp):
            left = cls._eval_node(node.left)
            right = cls._eval_node(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                if right == 0:
                    raise ValueError("Division by zero is not allowed.")
                if left % right != 0:
                    raise ValueError("Non-integer division results are not allowed.")
                return left // right
            raise ValueError("Unsupported binary operator.")
        raise ValueError("Unsupported expression node.")


class CountingService:
    def __init__(
        self,
        *,
        bot: discord.Client,
        counting: CountingRepository,
        guild_settings: GuildSettingsRepository,
        dommes: DommesRepository,
        bot_settings: BotSettingsRepository | None = None,
        subs: SubsRepository | None = None,
        rescue_window_seconds: int = 300,
        rescue_tick_seconds: int = 15,
        block_seconds: int = 12 * 60 * 60,
        parse_test_sends_as_real_sends: bool = False,
        test_gifter_usernames: tuple[str, ...] = (),
    ) -> None:
        self.bot = bot
        self.counting = counting
        self.guild_settings = guild_settings
        self.dommes = dommes
        self.bot_settings = bot_settings
        self.subs = subs
        self.rescue_window_seconds = rescue_window_seconds
        self.rescue_tick_seconds = rescue_tick_seconds
        self.block_seconds = block_seconds
        self.parse_test_sends_as_real_sends = parse_test_sends_as_real_sends
        self.test_gifter_usernames = set(test_gifter_usernames)

        self._runtime_windows: dict[int, _WindowRuntime] = {}
        self._ticker_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._ticker_task is not None:
            return
        self._ticker_task = asyncio.create_task(self._ticker_loop(), name="rob-count-recovery")
        await self._sync_recovery_windows()

    async def stop(self) -> None:
        if self._ticker_task is not None:
            self._ticker_task.cancel()
            try:
                await self._ticker_task
            except asyncio.CancelledError:
                pass
            self._ticker_task = None

    async def _ticker_loop(self) -> None:
        try:
            while True:
                await self._sync_recovery_windows()
                await asyncio.sleep(self.rescue_tick_seconds)
        except asyncio.CancelledError:
            return
        except Exception:
            log.exception("Count recovery ticker failed.")

    async def get_or_create_state(self, guild_id: int):
        settings = await self.guild_settings.get(guild_id)
        state = await self.counting.get(guild_id)
        if state is not None:
            configured_channel_id = settings.counting_channel_id if settings is not None else None
            if configured_channel_id is not None and (
                state.channel_id != configured_channel_id or not state.is_enabled
            ):
                return await self.counting.upsert(
                    guild_id=guild_id,
                    channel_id=configured_channel_id,
                    current_number=state.current_number,
                    last_user_id=state.last_user_id,
                    is_enabled=True,
                    pending_restore=state.pending_restore,
                )
            return state
        channel_id = settings.counting_channel_id if settings is not None else None
        return await self.counting.upsert(
            guild_id=guild_id,
            channel_id=channel_id,
            current_number=0,
            last_user_id=None,
            is_enabled=channel_id is not None,
            pending_restore=False,
        )

    async def set_current_number(self, guild_id: int, number: int):
        state = await self.get_or_create_state(guild_id)
        await self._cancel_active_windows_for_guild(guild_id)
        return await self.counting.upsert(
            guild_id=guild_id,
            channel_id=state.channel_id,
            current_number=max(0, number),
            last_user_id=None,
            is_enabled=True,
            pending_restore=False,
        )

    async def process_message(self, message: discord.Message) -> CountingProcessResult | None:
        if message.guild is None or message.author.bot:
            return None

        # Wind-down phase 3: Rob is fully offline, the count stops too.
        if self.bot_settings is not None and await wind_down.get_phase(self.bot_settings) >= 3:
            return None

        state = await self.get_or_create_state(message.guild.id)
        if not state.is_enabled or state.channel_id is None:
            return None
        if message.channel.id != state.channel_id:
            return None

        recovery_disabled = await self._recovery_disabled_for_guild(message.guild.id)
        if not recovery_disabled:
            await self._sync_recovery_windows()

        settings = await self.guild_settings.get(message.guild.id)
        is_sub = self._has_role(message.author, settings.sub_role_id if settings else None)
        is_domme = self._has_role(message.author, settings.domme_role_id if settings else None)

        if is_sub:
            block = await self.counting.get_active_block(message.guild.id, message.author.id)
            if block is not None:
                return CountingProcessResult(
                    success=False,
                    expected_number=state.current_number + 1,
                    current_number=state.current_number,
                    reason="blocked_sub",
                    blocked_until=block.blocked_until,
                )

        now = datetime.now(timezone.utc)
        if not recovery_disabled:
            active_window = await self.counting.get_active_recovery_window(message.guild.id, message.channel.id)
            if active_window is not None and active_window.expires_at > now:
                return CountingProcessResult(
                    success=False,
                    expected_number=state.current_number + 1,
                    current_number=state.current_number,
                    reason="paused_for_rescue",
                )
            if active_window is not None and active_window.expires_at <= now:
                await self._expire_window(active_window)
                state = await self.get_or_create_state(message.guild.id)

        if getattr(message, "attachments", None) or getattr(message, "stickers", None):
            return None

        content = message.content.strip()
        is_attempt, number = self._parse_count_attempt(content)
        if not is_attempt:
            return None

        expected = state.current_number + 1
        if state.last_user_id == message.author.id:
            return CountingProcessResult(
                success=False,
                expected_number=expected,
                current_number=state.current_number,
                reason="same_user",
            )

        if number != expected:
            if recovery_disabled:
                await self._cancel_active_windows_for_guild(message.guild.id)
                await self.counting.upsert(
                    guild_id=state.guild_id,
                    channel_id=state.channel_id,
                    current_number=0,
                    last_user_id=None,
                    is_enabled=state.is_enabled,
                    pending_restore=False,
                )
                return CountingProcessResult(
                    success=False,
                    expected_number=expected,
                    current_number=0,
                    reason="wrong_number_reset",
                    reactions=self._failure_reactions(),
                )

            deadline = now + timedelta(seconds=self.rescue_window_seconds)
            if is_domme:
                domme_record = await self.dommes.get_by_user_id(message.guild.id, message.author.id)
                window = await self._start_recovery_window(
                    guild_id=message.guild.id,
                    channel_id=message.channel.id,
                    failed_user_id=message.author.id,
                    failed_user_role="domme",
                    required_domme_user_id=message.author.id,
                    required_domme_id=domme_record.id if domme_record is not None else None,
                    expected_number=expected,
                    attempted_content=content,
                    deadline=deadline,
                    claimed_restriction=False,
                    claimed_unresolved=False,
                )
                await self._ensure_window_message(window, force_post_if_missing=True)
                runtime = self._runtime_windows.get(window.id)
                if runtime is not None:
                    runtime.failed_message = message
                return CountingProcessResult(
                    success=False,
                    expected_number=expected,
                    current_number=state.current_number,
                    reason="wrong_number_domme_recovery",
                    deadline=deadline,
                    reactions=self._failure_reactions(),
                )

            if is_sub:
                if message.author.id == _SPECIAL_SUB_USER_ID:
                    sub_required_domme_user_id: int | None = _SPECIAL_SUB_REQUIRED_DOMME_USER_ID
                else:
                    sub_required_domme_user_id = None
                window = await self._start_recovery_window(
                    guild_id=message.guild.id,
                    channel_id=message.channel.id,
                    failed_user_id=message.author.id,
                    failed_user_role="sub",
                    required_domme_user_id=sub_required_domme_user_id,
                    required_domme_id=None,
                    expected_number=expected,
                    attempted_content=content,
                    deadline=deadline,
                    claimed_restriction=False,
                    claimed_unresolved=False,
                )
                await self._ensure_window_message(window, force_post_if_missing=True)
                runtime = self._runtime_windows.get(window.id)
                if runtime is not None:
                    runtime.failed_message = message
                return CountingProcessResult(
                    success=False,
                    expected_number=expected,
                    current_number=state.current_number,
                    reason="wrong_number_sub_recovery",
                    deadline=deadline,
                    reactions=self._failure_reactions(),
                )

            await self._cancel_active_windows_for_guild(message.guild.id)
            await self.counting.upsert(
                guild_id=state.guild_id,
                channel_id=state.channel_id,
                current_number=0,
                last_user_id=None,
                is_enabled=state.is_enabled,
                pending_restore=False,
            )
            return CountingProcessResult(
                success=False,
                expected_number=expected,
                current_number=0,
                reason="wrong_number_reset",
                reactions=self._failure_reactions(),
            )

        await self._cancel_active_windows_for_guild(message.guild.id)
        await self.counting.upsert(
            guild_id=state.guild_id,
            channel_id=state.channel_id,
            current_number=number,
            last_user_id=message.author.id,
            is_enabled=state.is_enabled,
            pending_restore=False,
        )
        reactions = await self._build_success_reactions(
            guild_id=message.guild.id,
            number=number,
        )
        return CountingProcessResult(
            success=True,
            expected_number=expected,
            current_number=number,
            reactions=reactions,
        )

    async def process_send_for_count_rescue(self, send) -> bool:
        if await self._recovery_disabled_for_guild(send.guild_id):
            return False
        await self._sync_recovery_windows()
        windows = [
            window
            for window in await self.counting.list_active_recovery_windows()
            if window.guild_id == send.guild_id
        ]
        if not windows:
            return False

        now = datetime.now(timezone.utc)
        send_time = getattr(send, "sent_at", None) or now
        for window in windows:
            if window.expires_at <= now:
                continue
            failed_sub_send_names: frozenset[str] | None = None
            if (
                window.failed_user_role == "sub"
                and send.sub_user_id != window.failed_user_id
                and send.sub_name is not None
                and self.subs is not None
            ):
                sub_send_name_records = await self.subs.list_send_names_for_user(
                    window.guild_id, window.failed_user_id
                )
                failed_sub_send_names = frozenset(
                    record.send_name.casefold() for record in sub_send_name_records
                )
            if not self._send_qualifies_for_window(
                send=send, window=window, send_time=send_time, failed_sub_send_names=failed_sub_send_names
            ):
                continue
            resolved = await self.counting.resolve_recovery_window(window_id=window.id, resolution="recovered")
            if not resolved:
                continue
            state = await self.get_or_create_state(window.guild_id)
            await self.counting.upsert(
                guild_id=state.guild_id,
                channel_id=state.channel_id,
                current_number=max(0, window.expected_number - 1),
                last_user_id=None,
                is_enabled=state.is_enabled,
                pending_restore=False,
            )
            runtime = self._runtime_windows.pop(window.id, None)
            message = runtime.message if runtime is not None else None
            failed_message = runtime.failed_message if runtime is not None else None
            if message is not None:
                try:
                    await message.edit(**count_saved_card(next_number=window.expected_number).edit_kwargs())
                except discord.HTTPException:
                    pass
            if failed_message is not None:
                try:
                    await failed_message.delete()
                except discord.HTTPException:
                    pass
            return True
        return False

    async def _start_recovery_window(
        self,
        *,
        guild_id: int,
        channel_id: int,
        failed_user_id: int,
        failed_user_role: str,
        required_domme_user_id: int | None,
        required_domme_id: int | None,
        expected_number: int,
        attempted_content: str,
        deadline: datetime,
        claimed_restriction: bool,
        claimed_unresolved: bool,
    ) -> CountRecoveryWindow:
        await self._cancel_active_windows_for_guild(guild_id)
        state = await self.get_or_create_state(guild_id)
        await self.counting.upsert(
            guild_id=state.guild_id,
            channel_id=state.channel_id,
            current_number=state.current_number,
            last_user_id=state.last_user_id,
            is_enabled=state.is_enabled,
            pending_restore=True,
        )
        now = datetime.now(timezone.utc)
        window = await self.counting.create_recovery_window(
            guild_id=guild_id,
            channel_id=channel_id,
            failed_user_id=failed_user_id,
            failed_user_role=failed_user_role,
            required_domme_user_id=required_domme_user_id,
            required_domme_id=required_domme_id,
            expected_number=expected_number,
            attempted_content=attempted_content,
            started_at=now,
            expires_at=deadline,
        )
        runtime = _WindowRuntime(window_id=window.id, guild_id=guild_id, channel_id=channel_id)
        self._runtime_windows[window.id] = runtime
        if claimed_restriction and required_domme_user_id is None:
            # Keep the restriction fail-closed if claim could not be resolved uniquely.
            await self.counting.resolve_recovery_window(window_id=window.id, resolution="cancelled")
            expires = now + timedelta(seconds=self.rescue_window_seconds)
            window = await self.counting.create_recovery_window(
                guild_id=guild_id,
                channel_id=channel_id,
                failed_user_id=failed_user_id,
                failed_user_role=failed_user_role,
                required_domme_user_id=_CLAIM_UNRESOLVED_SENTINEL,
                required_domme_id=None,
                expected_number=expected_number,
                attempted_content=attempted_content,
                started_at=now,
                expires_at=expires,
            )
            self._runtime_windows[window.id] = _WindowRuntime(window_id=window.id, guild_id=guild_id, channel_id=channel_id)
        if claimed_unresolved and window.required_domme_user_id is None:
            # Safety fallback; unresolved claim should always fail closed.
            await self.counting.resolve_recovery_window(window_id=window.id, resolution="cancelled")
            window = await self.counting.create_recovery_window(
                guild_id=guild_id,
                channel_id=channel_id,
                failed_user_id=failed_user_id,
                failed_user_role=failed_user_role,
                required_domme_user_id=_CLAIM_UNRESOLVED_SENTINEL,
                required_domme_id=None,
                expected_number=expected_number,
                attempted_content=attempted_content,
                started_at=now,
                expires_at=deadline,
            )
            self._runtime_windows[window.id] = _WindowRuntime(window_id=window.id, guild_id=guild_id, channel_id=channel_id)
        return window

    async def _sync_recovery_windows(self) -> None:
        active_windows = [
            window
            for window in await self.counting.list_active_recovery_windows()
            if not await self._recovery_disabled_for_guild(window.guild_id)
        ]
        active_ids = {window.id for window in active_windows}
        for window in list(active_windows):
            if window.expires_at <= datetime.now(timezone.utc):
                await self._expire_window(window)
                continue
            await self._ensure_pending_restore_flag(window.guild_id)
            await self._ensure_window_message(window, force_post_if_missing=False)
        stale_ids = [window_id for window_id in self._runtime_windows.keys() if window_id not in active_ids]
        for window_id in stale_ids:
            self._runtime_windows.pop(window_id, None)

    async def _expire_window(self, window: CountRecoveryWindow) -> None:
        if window.failed_user_role == "domme":
            resolved = await self.counting.resolve_recovery_window(window_id=window.id, resolution="expired_reset")
        else:
            resolved = await self.counting.resolve_recovery_window(window_id=window.id, resolution="expired_blocked")
        if not resolved:
            self._runtime_windows.pop(window.id, None)
            return

        state = await self.get_or_create_state(window.guild_id)
        runtime = self._runtime_windows.pop(window.id, None)
        message = runtime.message if runtime is not None else None

        if window.failed_user_role == "domme":
            await self.counting.upsert(
                guild_id=state.guild_id,
                channel_id=state.channel_id,
                current_number=0,
                last_user_id=None,
                is_enabled=state.is_enabled,
                pending_restore=False,
            )
            if message is not None:
                try:
                    await message.edit(**count_failed_reset_card().edit_kwargs())
                except discord.HTTPException:
                    pass
            return

        await self.counting.upsert(
            guild_id=state.guild_id,
            channel_id=state.channel_id,
            current_number=max(0, window.expected_number - 1),
            last_user_id=None,
            is_enabled=state.is_enabled,
            pending_restore=False,
        )
        blocked_until = datetime.now(timezone.utc) + timedelta(seconds=self.block_seconds)
        await self.counting.upsert_block(
            guild_id=window.guild_id,
            discord_user_id=window.failed_user_id,
            reason="count_recovery_missed",
            blocked_until=blocked_until,
        )
        if message is not None:
            try:
                await message.edit(**count_failed_sub_blocked_card(blocked_until_unix=int(blocked_until.timestamp())).edit_kwargs())
            except discord.HTTPException:
                pass

    async def _cancel_active_windows_for_guild(self, guild_id: int) -> None:
        windows = await self.counting.list_active_recovery_windows()
        for window in windows:
            if window.guild_id != guild_id:
                continue
            await self.counting.resolve_recovery_window(window_id=window.id, resolution="cancelled")
            self._runtime_windows.pop(window.id, None)

    async def _ensure_pending_restore_flag(self, guild_id: int) -> None:
        state = await self.get_or_create_state(guild_id)
        if state.pending_restore:
            return
        await self.counting.upsert(
            guild_id=state.guild_id,
            channel_id=state.channel_id,
            current_number=state.current_number,
            last_user_id=state.last_user_id,
            is_enabled=state.is_enabled,
            pending_restore=True,
        )

    async def _ensure_window_message(self, window: CountRecoveryWindow, *, force_post_if_missing: bool) -> None:
        runtime = self._runtime_windows.get(window.id)
        if runtime is None:
            runtime = _WindowRuntime(
                window_id=window.id,
                guild_id=window.guild_id,
                channel_id=window.channel_id,
            )
            self._runtime_windows[window.id] = runtime

        remaining = max(0, int((window.expires_at - datetime.now(timezone.utc)).total_seconds()))
        kwargs = count_rescue_needed_for_role_card(
            remaining_seconds=remaining,
            deadline_unix=int(window.expires_at.timestamp()),
            failed_user_role=window.failed_user_role,
            claimed_restriction=window.failed_user_role == "sub" and window.required_domme_user_id not in (None, _CLAIM_UNRESOLVED_SENTINEL),
            claimed_unresolved=window.failed_user_role == "sub" and window.required_domme_user_id == _CLAIM_UNRESOLVED_SENTINEL,
        )

        if runtime.message is not None:
            try:
                await runtime.message.edit(**kwargs.edit_kwargs())
                return
            except discord.HTTPException:
                runtime.message = None

        channel = await self._resolve_channel(window.guild_id, window.channel_id)
        if channel is None:
            return
        try:
            runtime.message = await channel.send(**kwargs.send_kwargs())
        except discord.HTTPException:
            runtime.message = None

    async def _resolve_channel(self, guild_id: int, channel_id: int):
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return None
        channel = guild.get_channel(channel_id)
        if channel is not None:
            return channel
        try:
            fetched = await guild.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            return None
        return fetched

    def _send_qualifies_for_window(
        self,
        *,
        send,
        window: CountRecoveryWindow,
        send_time: datetime,
        failed_sub_send_names: frozenset[str] | None = None,
    ) -> bool:
        if send.is_private:
            return False
        if send.is_test_send and not self.parse_test_sends_as_real_sends:
            return False
        if not self.parse_test_sends_as_real_sends and is_known_test_sender(
            send.sub_name,
            test_gifter_usernames=self.test_gifter_usernames,
        ):
            return False
        if send_time < window.started_at or send_time > window.expires_at:
            return False

        if window.failed_user_role == "domme":
            return send.domme_user_id == window.required_domme_user_id

        if send.sub_user_id != window.failed_user_id:
            if (
                failed_sub_send_names is None
                or send.sub_name is None
                or send.sub_name.casefold() not in failed_sub_send_names
            ):
                return False
        required_domme = window.required_domme_user_id
        if required_domme is None:
            return True
        if required_domme == _CLAIM_UNRESOLVED_SENTINEL:
            return False
        return send.domme_user_id == required_domme

    async def _resolve_claim_requirement(
        self,
        *,
        guild: discord.Guild,
        guild_id: int,
        member: discord.Member | None,
    ) -> _ClaimResolution:
        if member is None:
            return _ClaimResolution(
                required_domme_user_id=None,
                required_domme_id=None,
                claimed_restriction=False,
                claimed_unresolved=False,
            )

        prefix = _DEFAULT_CLAIMED_ROLE_PREFIX
        if self.bot_settings is not None:
            configured = await self.bot_settings.get_text(_CLAIMED_ROLE_PREFIX_KEY)
            if configured:
                prefix = configured

        claimed_labels = [
            role.name[len(prefix) :].strip()
            for role in getattr(member, "roles", [])
            if getattr(role, "name", "").lower().startswith(prefix.lower())
            and len(getattr(role, "name", "")) > len(prefix)
        ]
        if not claimed_labels:
            return _ClaimResolution(
                required_domme_user_id=None,
                required_domme_id=None,
                claimed_restriction=False,
                claimed_unresolved=False,
            )

        if len(claimed_labels) > 1:
            return _ClaimResolution(
                required_domme_user_id=_CLAIM_UNRESOLVED_SENTINEL,
                required_domme_id=None,
                claimed_restriction=True,
                claimed_unresolved=True,
            )

        label = claimed_labels[0].casefold()
        dommes = await self.dommes.list_for_guild(guild_id)
        matches = []
        for domme in dommes:
            candidates = set()
            if domme.public_display_name:
                candidates.add(domme.public_display_name.casefold())
            if domme.throne_handle:
                candidates.add(domme.throne_handle.casefold())
            domme_member = guild.get_member(domme.discord_user_id)
            if domme_member is not None:
                candidates.add(domme_member.display_name.casefold())
                candidates.add(domme_member.name.casefold())
            if label in candidates:
                matches.append(domme)

        if len(matches) != 1:
            return _ClaimResolution(
                required_domme_user_id=_CLAIM_UNRESOLVED_SENTINEL,
                required_domme_id=None,
                claimed_restriction=True,
                claimed_unresolved=True,
            )

        domme = matches[0]
        return _ClaimResolution(
            required_domme_user_id=domme.discord_user_id,
            required_domme_id=domme.id,
            claimed_restriction=True,
            claimed_unresolved=False,
        )

    @staticmethod
    def _has_role(user, role_id: int | None) -> bool:
        if role_id is None or not isinstance(user, discord.Member):
            return False
        return any(role.id == role_id for role in user.roles)

    @staticmethod
    def _parse_count_attempt(content: str) -> tuple[bool, int]:
        stripped = content.strip()
        if not stripped:
            return False, 0
        if len(stripped) > _MAX_COUNT_EXPRESSION_LENGTH:
            return False, 0
        if not any(ch.isdigit() for ch in stripped):
            return False, 0
        if any(ch not in _ALLOWED_EXPRESSION_CHARS for ch in stripped):
            return False, 0
        try:
            return True, _ExpressionEvaluator.evaluate(stripped)
        except ValueError:
            return False, 0

    @staticmethod
    def evaluate_expression(content: str) -> int:
        return _ExpressionEvaluator.evaluate(content)

    async def _recovery_disabled_for_guild(self, guild_id: int | None) -> bool:
        maintenance = getattr(self.bot, "maintenance_service", None)
        if maintenance is None:
            return False
        checker = getattr(maintenance, "count_recovery_disabled_for_guild", None)
        if checker is None:
            return False
        result = checker(guild_id)
        if inspect.isawaitable(result):
            return bool(await result)
        if isinstance(result, bool):
            return result
        return False

    async def get_active_recovery_windows(self) -> list[CountRecoveryWindow]:
        return await self.counting.list_active_recovery_windows()

    async def resolve_expired_windows_once(self) -> None:
        await self._sync_recovery_windows()

    async def _build_success_reactions(self, *, guild_id: int, number: int) -> tuple[str, ...]:
        await self._update_high_watermark_if_needed(guild_id=guild_id, number=number)
        return (ROBYES,)

    @staticmethod
    def _failure_reactions() -> tuple[str, str]:
        return (ROBBLANK, ROBNO)

    async def _update_high_watermark_if_needed(self, *, guild_id: int, number: int) -> bool:
        if self.bot_settings is None:
            return False
        key = f"{_COUNT_HIGH_WATERMARK_KEY_PREFIX}{guild_id}"
        current_value = await self.bot_settings.get_text(key)
        try:
            current_high = int(current_value) if current_value is not None else 0
        except (TypeError, ValueError):
            current_high = 0
        if number <= current_high:
            return False
        await self.bot_settings.set_value(key, str(number))
        return True
