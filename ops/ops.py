from __future__ import annotations

import argparse
import asyncio
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime

import aiohttp
import discord

from rob.config.settings import configure_logging, load_base_settings
from rob.database.connection import Database
from rob.database.repositories import (
    BlacklistRepository,
    BotStateRepository,
    CountingRepository,
    DommesRepository,
    GuildSettingsRepository,
    InactiveUsersRepository,
    LeaderboardsRepository,
    SendsRepository,
    ServerBackupsRepository,
    SubsRepository,
    ThroneCreatorsRepository,
)
from rob.services.maintenance_service import MaintenanceService
from rob.services.registration_service import RegistrationService
from rob.services.send_service import SendService
from rob.services.throne_service import ThroneService
from rob.throne.security import hash_webhook_secret
from rob.utils.money import format_money_from_cents, format_money_with_currency_name
from rob.utils.money import dollars_to_cents


_USER_REF_RE = re.compile(r"<@!?(\d+)>")

GUILD_CHANNEL_FIELDS = (
    "registration_channel_id",
    "leaderboard_channel_id",
    "send_track_channel_id",
    "counting_channel_id",
    "report_channel_id",
    "warn_log_channel_id",
    "backup_approval_channel_id",
)

GUILD_CHANNEL_LABELS = {
    "registration_channel_id": "Registration Channel",
    "leaderboard_channel_id": "Leaderboard Channel",
    "send_track_channel_id": "Send Tracker Channel",
    "counting_channel_id": "Counting Channel",
    "report_channel_id": "Report Channel",
    "warn_log_channel_id": "Warn Log Channel",
    "backup_approval_channel_id": "Backup Approval Channel",
}

GUILD_CHANNEL_MATCH_TOKENS = {
    "registration_channel_id": ("registration", "register", "setup", "welcome"),
    "leaderboard_channel_id": ("leaderboard", "rank", "leader-board"),
    "send_track_channel_id": ("send-tracker", "send-tracking", "sendtracker", "throne", "sends"),
    "counting_channel_id": ("counting", "count"),
    "report_channel_id": ("report", "support", "help"),
    "warn_log_channel_id": ("warn", "warning", "mod-log", "logs", "log"),
    "backup_approval_channel_id": (
        "backup-approval",
        "backup-approvals",
        "server-backup",
        "backup",
        "backups",
        "mod-approval",
        "approvals",
    ),
}

GUILD_ROLE_FIELDS = (
    "domme_role_id",
    "sub_role_id",
    "mod_role_id",
    "inactive_role_id",
    "leaderboard_view_role_id",
    "active_role_id",
    "unverified_role_id",
    "trial_mod_role_id",
)

GUILD_ROLE_LABELS = {
    "domme_role_id": "Dom/me Role",
    "sub_role_id": "Sub Role",
    "mod_role_id": "Moderator Role",
    "inactive_role_id": "Inactive Role",
    "leaderboard_view_role_id": "Leaderboard Access Role",
    "active_role_id": "Active Role",
    "unverified_role_id": "Unverified Role",
    "trial_mod_role_id": "Trial Moderator Role",
}

GUILD_ROLE_MATCH_TOKENS = {
    "domme_role_id": ("domme", "dom/me", "dom", "dommes"),
    "sub_role_id": ("sub", "subs"),
    "mod_role_id": ("mod", "mods", "moderator", "staff", "admin"),
    "inactive_role_id": ("inactive", "inactivity", "away"),
    "leaderboard_view_role_id": (
        "leaderboard access",
        "leaderboard",
        "leader board",
        "board access",
        "vip",
    ),
    "active_role_id": ("active", "active member", "verified member"),
    "unverified_role_id": ("unverified", "unverify", "not-verified", "pending", "newcomer"),
    "trial_mod_role_id": ("trial mod", "trialmod", "trial-mod", "trial moderator", "trial"),
}


@dataclass(frozen=True)
class LiveGuildChannel:
    channel_id: int
    name: str
    kind: str


@dataclass(frozen=True)
class LiveGuildRole:
    role_id: int
    name: str


@dataclass(frozen=True)
class LiveGuildScanResult:
    guild_id: int
    guild_name: str | None
    channels: tuple[LiveGuildChannel, ...]
    roles: tuple[LiveGuildRole, ...]
    source: str
    error: str | None = None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rob backend operations.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("status", help="Show database, maintenance, and queue status.")

    maintenance = subparsers.add_parser("maintenance", help="Manage maintenance mode.")
    maintenance_subparsers = maintenance.add_subparsers(dest="maintenance_command", required=True)
    maintenance_subparsers.add_parser("status", help="Show maintenance mode state.")
    maintenance_on = maintenance_subparsers.add_parser("on", help="Enable maintenance mode.")
    maintenance_on.add_argument("reason", nargs="?", default="", help="Optional maintenance reason.")
    maintenance_subparsers.add_parser("off", help="Disable maintenance mode.")

    queue = subparsers.add_parser("queue", help="Inspect or release queued sends.")
    queue_subparsers = queue.add_subparsers(dest="queue_command", required=True)
    queue_subparsers.add_parser("status", help="Show queue counts.")
    queue_subparsers.add_parser("flush", help="Release queued maintenance sends to pending.")

    leaderboard = subparsers.add_parser("leaderboard", help="Leaderboard operations.")
    leaderboard_subparsers = leaderboard.add_subparsers(dest="leaderboard_command", required=True)
    leaderboard_subparsers.add_parser("refresh", help="Request a leaderboard refresh from the bot.")
    leaderboard_adopt = leaderboard_subparsers.add_parser(
        "adopt",
        help="Adopt existing Discord leaderboard messages into DB refs.",
    )
    leaderboard_adopt.add_argument("--guild-id", type=int, required=True)
    leaderboard_adopt.add_argument("--leaderboard-channel-id", type=int, required=True)
    leaderboard_adopt.add_argument("--leaderboard-message-id", type=int, required=True)
    leaderboard_adopt.add_argument("--stats-message-id", type=int, required=True)
    leaderboard_status = leaderboard_subparsers.add_parser("status", help="Show leaderboard status summary.")
    leaderboard_status.add_argument("--guild-id", type=int, default=None)
    leaderboard_preview = leaderboard_subparsers.add_parser("preview", help="Preview top leaderboard rows.")
    leaderboard_preview.add_argument("--guild-id", type=int, default=None)
    diagnose = leaderboard_subparsers.add_parser("diagnose", help="Diagnose leaderboard send matching.")

    diagnose.add_argument("--guild-id", type=int, default=None)
    repair = leaderboard_subparsers.add_parser(
        "repair-send-dommes",
        help="Repair sends.domme_user_id from dommes.id matches.",
    )
    repair.add_argument("--guild-id", type=int, default=None)
    repair.add_argument("--dry-run", action="store_true", help="Preview changes without writing.")

    throne = subparsers.add_parser("throne", help="Throne send operations.")
    throne_subparsers = throne.add_subparsers(dest="throne_command", required=True)
    throne_subparsers.add_parser(
        "refresh",
        help="Show current webhook-only tracking mode guidance (legacy alias).",
    )
    throne_status = throne_subparsers.add_parser("status", help="Show Throne creator status.")
    throne_status.add_argument("--guild-id", type=int, default=None)
    throne_status.add_argument("--handle", type=str, default=None)
    throne_dommes = throne_subparsers.add_parser("dommes", help="List registered Throne creators.")
    throne_dommes.add_argument("--guild-id", type=int, default=None)
    throne_list = throne_subparsers.add_parser(
        "list",
        help="List registered Throne creators (legacy alias for dommes).",
    )
    throne_list.add_argument("--guild-id", type=int, default=None)
    throne_search = throne_subparsers.add_parser(
        "search",
        help="Show one Throne creator record by Discord user ref.",
    )
    throne_search.add_argument("user_ref", type=str)
    throne_search.add_argument("--guild-id", type=int, default=None)

    throne_webhook = throne_subparsers.add_parser("webhook", help="Webhook secret operations.")
    throne_webhook_subparsers = throne_webhook.add_subparsers(
        dest="throne_webhook_command",
        required=True,
    )
    throne_webhook_refresh = throne_webhook_subparsers.add_parser(
        "refresh",
        help="Rotate webhook secret for one registered creator.",
    )
    throne_webhook_refresh.add_argument("user_ref", type=str)
    throne_webhook_refresh.add_argument("--guild-id", type=int, default=None)

    throne_addsend = throne_subparsers.add_parser(
        "addsend",
        help="Insert a manual send for a Dom/me (legacy compatibility command).",
    )
    throne_addsend.add_argument("user_ref", type=str)
    throne_addsend.add_argument("amount", type=float)
    throne_addsend.add_argument("--guild-id", type=int, default=None)
    throne_addsend.add_argument("--sub-name", type=str, default=None)
    throne_addsend.add_argument("--method", type=str, default="manual")
    throne_addsend.add_argument("--currency", type=str, default="USD")
    throne_addsend.add_argument("--note", type=str, default=None)

    throne_addsub = throne_subparsers.add_parser(
        "addsub",
        help="Register/update a Sub sending name for a Discord user.",
    )
    throne_addsub.add_argument("user_ref", type=str)
    throne_addsub.add_argument("name", type=str)
    throne_addsub.add_argument("--guild-id", type=int, default=None)

    throne_adddomme = throne_subparsers.add_parser(
        "adddomme",
        help="Register/update a Dom/me from a Throne URL/handle.",
    )
    throne_adddomme.add_argument("user_ref", type=str)
    throne_adddomme.add_argument("throne_input", type=str)
    throne_adddomme.add_argument("--guild-id", type=int, default=None)

    throne_subs = throne_subparsers.add_parser("subs", help="List registered subs.")
    throne_subs.add_argument("--guild-id", type=int, default=None)
    throne_subparsers.add_parser(
        "invalidate-test-sends",
        help="Mark known Throne test-user sends so leaderboards can exclude them.",
    )

    blacklist = subparsers.add_parser("blacklist", help="Manage Rob global blacklist.")
    blacklist_subparsers = blacklist.add_subparsers(dest="blacklist_command", required=True)
    blacklist_add = blacklist_subparsers.add_parser("add", help="Add a user to blacklist.")
    blacklist_add.add_argument("discord_user_id", type=int)
    blacklist_add.add_argument("--reason", type=str, default="manual")
    blacklist_add.add_argument("--created-by", type=int, default=None)
    blacklist_remove = blacklist_subparsers.add_parser("remove", help="Remove a user from blacklist.")
    blacklist_remove.add_argument("discord_user_id", type=int)
    blacklist_list = blacklist_subparsers.add_parser("list", help="List blacklist entries.")
    blacklist_list.add_argument("--limit", type=int, default=100)

    sends = subparsers.add_parser("sends", help="Send record operations.")
    sends_subparsers = sends.add_subparsers(dest="sends_command", required=True)
    sends_list = sends_subparsers.add_parser("list", help="List recent sends.")
    sends_list.add_argument(
        "--status",
        choices=["pending", "posted", "failed", "queued_maintenance", "ignored", "all"],
        default="all",
    )
    sends_list.add_argument("--guild-id", type=int, default=None)
    sends_list.add_argument("--limit", type=int, default=25)
    sends_subparsers.add_parser(
        "backfill-public-ids",
        help="Generate and store missing public send IDs.",
    )
    sends_repair_mentions = sends_subparsers.add_parser(
        "repair-mentions",
        help="Link legacy sends whose sub name is a raw @mention to that user.",
    )
    sends_repair_mentions.add_argument("--guild-id", type=int, default=None)
    sends_repair_mentions.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing.",
    )
    sends_mark_posted = sends_subparsers.add_parser(
        "mark-posted",
        help="Force mark a send as posted.",
    )
    sends_mark_posted.add_argument("send_id", type=int)

    guild = subparsers.add_parser("guild", help="Guild settings inspection and repair.")
    guild_subparsers = guild.add_subparsers(dest="guild_command", required=True)
    guild_scan = guild_subparsers.add_parser(
        "scan",
        help="Inspect guild channel settings and suggest DB update commands.",
    )
    guild_scan.add_argument("--guild-id", type=int, required=True)
    guild_set_channel = guild_subparsers.add_parser(
        "set-channel",
        help="Update one guild_settings channel field.",
    )
    guild_set_channel.add_argument("--guild-id", type=int, required=True)
    guild_set_channel.add_argument(
        "--field",
        choices=GUILD_CHANNEL_FIELDS,
        required=True,
    )
    guild_set_channel_group = guild_set_channel.add_mutually_exclusive_group(required=True)
    guild_set_channel_group.add_argument("--channel-id", type=int)
    guild_set_channel_group.add_argument("--clear", action="store_true")
    guild_set_role = guild_subparsers.add_parser(
        "set-role",
        help="Update one guild_settings role field.",
    )
    guild_set_role.add_argument("--guild-id", type=int, required=True)
    guild_set_role.add_argument(
        "--field",
        choices=GUILD_ROLE_FIELDS,
        required=True,
    )
    guild_set_role_group = guild_set_role.add_mutually_exclusive_group(required=True)
    guild_set_role_group.add_argument("--role-id", type=int)
    guild_set_role_group.add_argument("--clear", action="store_true")

    count = subparsers.add_parser("count", help="Counting operations.")
    count_subparsers = count.add_subparsers(dest="count_command", required=True)
    count_status = count_subparsers.add_parser("status", help="Show counting state.")
    count_status.add_argument("--guild-id", type=int, default=None)
    count_set = count_subparsers.add_parser("set", help="Set the current counting number.")
    count_set.add_argument("number", type=int)
    count_set.add_argument("--guild-id", type=int, default=None)

    inactivity = subparsers.add_parser("inactivity", help="Activity / inactive-role system.")
    inactivity_subparsers = inactivity.add_subparsers(dest="inactivity_command", required=True)
    inactivity_status = inactivity_subparsers.add_parser("status", help="Show inactivity system state.")
    inactivity_status.add_argument("--guild-id", type=int, default=None)
    inactivity_on = inactivity_subparsers.add_parser("on", help="Enable the inactivity system for a guild.")
    inactivity_on.add_argument("--guild-id", type=int, default=None)
    inactivity_off = inactivity_subparsers.add_parser("off", help="Disable the inactivity system for a guild.")
    inactivity_off.add_argument("--guild-id", type=int, default=None)

    backup = subparsers.add_parser("backup", help="Hourly server-backup system.")
    backup_subparsers = backup.add_subparsers(dest="backup_command", required=True)
    backup_status = backup_subparsers.add_parser("status", help="Show server-backup state.")
    backup_status.add_argument("--guild-id", type=int, default=None)
    backup_on = backup_subparsers.add_parser("on", help="Enable hourly server backups for a guild.")
    backup_on.add_argument("--guild-id", type=int, default=None)
    backup_off = backup_subparsers.add_parser("off", help="Disable hourly server backups for a guild.")
    backup_off.add_argument("--guild-id", type=int, default=None)
    backup_run = backup_subparsers.add_parser("run", help="Run one backup cycle now (via the running bot).")
    backup_run.add_argument("--guild-id", type=int, default=None)

    return parser


@dataclass(frozen=True)
class OperationsContext:
    settings: object
    database: Database
    bot_state: BotStateRepository
    maintenance: MaintenanceService
    sends: SendsRepository
    dommes: DommesRepository
    subs: SubsRepository
    throne_creators: ThroneCreatorsRepository
    blacklist: BlacklistRepository
    leaderboards: LeaderboardsRepository
    guild_settings: GuildSettingsRepository
    counting: CountingRepository
    server_backups: ServerBackupsRepository
    inactive_users: InactiveUsersRepository
    throne_service: ThroneService
    registration_service: RegistrationService
    send_service: SendService


async def create_context() -> OperationsContext:
    settings = load_base_settings()
    configure_logging(os.getenv("ROB_OPS_LOG_LEVEL", "WARNING"))
    database = Database(settings.database_url)
    await database.connect()
    bot_state = BotStateRepository(database)
    maintenance = MaintenanceService(bot_state)
    throne_service = ThroneService()
    guild_settings = GuildSettingsRepository(database)
    dommes = DommesRepository(database)
    subs = SubsRepository(database)
    throne_creators = ThroneCreatorsRepository(database)
    registration_service = RegistrationService(
        guild_settings=guild_settings,
        dommes=dommes,
        subs=subs,
        blacklist=BlacklistRepository(database),
        throne=throne_service,
        webhook_base_url=os.getenv("THRONE_WEBHOOK_BASE_URL") or None,
    )
    send_service = SendService(
        sends=SendsRepository(database),
        subs=subs,
        maintenance=maintenance,
        throne=throne_service,
        throne_test_gifter_usernames=settings.throne_test_gifter_usernames,
    )
    return OperationsContext(
        settings=settings,
        database=database,
        bot_state=bot_state,
        maintenance=maintenance,
        sends=send_service.sends,
        dommes=dommes,
        subs=subs,
        throne_creators=throne_creators,
        blacklist=registration_service.blacklist,
        leaderboards=LeaderboardsRepository(database),
        guild_settings=guild_settings,
        counting=CountingRepository(database),
        server_backups=ServerBackupsRepository(database),
        inactive_users=InactiveUsersRepository(database),
        throne_service=throne_service,
        registration_service=registration_service,
        send_service=send_service,
    )


async def resolve_guild_id(ctx: OperationsContext, guild_id: int | None) -> int:
    if guild_id is not None:
        return guild_id
    guild_ids = await ctx.guild_settings.list_guild_ids()
    if len(guild_ids) == 1:
        return guild_ids[0]
    if not guild_ids:
        raise RuntimeError("No guild_settings rows exist yet. Add one first or pass --guild-id.")
    raise RuntimeError("Multiple guilds exist. Pass --guild-id explicitly.")


def parse_user_ref(value: str) -> int | None:
    raw = value.strip()
    if raw.isdigit():
        return int(raw)
    match = _USER_REF_RE.fullmatch(raw)
    if match:
        return int(match.group(1))
    return None


def print_header(title: str) -> None:
    divider = "=" * 72
    print(divider)
    print(f"Rob Control | {title}")
    print(divider)


def print_field(label: str, value: object) -> None:
    print(f"- {label}: {value}")


def print_section(title: str) -> None:
    print()
    print(f"[{title}]")


def print_line(text: str = "") -> None:
    print(text)


def print_kv(_key: str, _value: object) -> None:
    return


def print_kv_raw(_text: str) -> None:
    return


def format_optional_datetime(value: datetime | None) -> str:
    if value is None:
        return "(none)"
    return value.isoformat()


def _normalize_channel_name(name: str) -> str:
    return name.strip().lower().replace("_", "-").replace(" ", "-")


def _score_named_match(name: str, tokens: tuple[str, ...]) -> int:
    normalized = _normalize_channel_name(name)
    score = 0
    for token in tokens:
        normalized_token = _normalize_channel_name(token)
        if normalized == normalized_token:
            score = max(score, 100)
        elif normalized.startswith(normalized_token):
            score = max(score, 75)
        elif normalized_token in normalized:
            score = max(score, 50)
    return score


def _find_best_channel_match(
    channels: tuple[LiveGuildChannel, ...],
    field_name: str,
) -> LiveGuildChannel | None:
    tokens = GUILD_CHANNEL_MATCH_TOKENS[field_name]
    scored: list[tuple[int, LiveGuildChannel]] = []
    for channel in channels:
        score = _score_named_match(channel.name, tokens)
        if score:
            scored.append((score, channel))
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], item[1].name, item[1].channel_id))
    return scored[0][1]


def _find_best_role_match(
    roles: tuple[LiveGuildRole, ...],
    field_name: str,
) -> LiveGuildRole | None:
    tokens = GUILD_ROLE_MATCH_TOKENS[field_name]
    scored: list[tuple[int, LiveGuildRole]] = []
    for role in roles:
        score = _score_named_match(role.name, tokens)
        if score:
            scored.append((score, role))
    if not scored:
        return None
    scored.sort(key=lambda item: (-item[0], item[1].name, item[1].role_id))
    return scored[0][1]


async def fetch_live_guild_scan(guild_id: int) -> LiveGuildScanResult:
    bot_scan = await fetch_live_guild_scan_from_bot_ops(guild_id)
    if bot_scan is not None:
        return bot_scan
    return await fetch_live_guild_scan_from_discord_rest(guild_id)


async def fetch_live_guild_scan_from_bot_ops(guild_id: int) -> LiveGuildScanResult | None:
    host = (os.getenv("ROB_OPS_HOST") or "127.0.0.1").strip()
    raw_port = (os.getenv("ROB_OPS_PORT") or "8811").strip()
    secret = (os.getenv("ROB_OPS_SECRET") or "").strip()

    try:
        port = int(raw_port)
    except ValueError:
        return None

    headers = {"User-Agent": "RobOps/1.0 (+https://github.com/patdadev/rob)"}
    if secret:
        headers["X-Rob-Ops-Secret"] = secret

    url = f"http://{host}:{port}/guilds/{guild_id}/scan"
    try:
        timeout = aiohttp.ClientTimeout(total=3)
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            async with session.get(url) as response:
                if response.status >= 400:
                    return None
                payload = await response.json()
    except (aiohttp.ClientError, TimeoutError):
        return None

    return LiveGuildScanResult(
        guild_id=int(payload["guild_id"]),
        guild_name=payload.get("guild_name"),
        channels=tuple(
            LiveGuildChannel(
                channel_id=int(channel["id"]),
                name=str(channel["name"]),
                kind=str(channel.get("kind", "unknown")),
            )
            for channel in payload.get("channels", [])
        ),
        roles=tuple(
            LiveGuildRole(
                role_id=int(role["id"]),
                name=str(role["name"]),
            )
            for role in payload.get("roles", [])
        ),
        source=str(payload.get("source") or "bot-session"),
        error=payload.get("error"),
    )


async def fetch_live_guild_scan_from_discord_rest(guild_id: int) -> LiveGuildScanResult:
    token = (os.getenv("DISCORD_TOKEN") or "").strip()
    if not token:
        return LiveGuildScanResult(
            guild_id=guild_id,
            guild_name=None,
            channels=(),
            roles=(),
            source="discord-rest",
            error="DISCORD_TOKEN is not configured for live guild scanning.",
        )

    headers = {
        "Authorization": f"Bot {token}",
        "User-Agent": "RobOps/1.0 (+https://github.com/patdadev/rob)",
    }
    api_base = "https://discord.com/api/v10"
    try:
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(f"{api_base}/guilds/{guild_id}") as guild_response:
                if guild_response.status >= 400:
                    detail = await guild_response.text()
                    return LiveGuildScanResult(
                        guild_id=guild_id,
                        guild_name=None,
                        channels=(),
                        roles=(),
                        source="discord-rest",
                        error=f"Discord scan failed: GET /guilds/{guild_id} returned {guild_response.status}: {detail}",
                    )
                guild_payload = await guild_response.json()

            async with session.get(f"{api_base}/guilds/{guild_id}/channels") as channels_response:
                if channels_response.status >= 400:
                    detail = await channels_response.text()
                    return LiveGuildScanResult(
                        guild_id=guild_id,
                        guild_name=guild_payload.get("name"),
                        channels=(),
                        roles=(),
                        source="discord-rest",
                        error=f"Discord scan failed: GET /guilds/{guild_id}/channels returned {channels_response.status}: {detail}",
                    )
                channels_payload = await channels_response.json()
    except aiohttp.ClientError as exc:
        return LiveGuildScanResult(
            guild_id=guild_id,
            guild_name=None,
            channels=(),
            roles=(),
            source="discord-rest",
            error=f"Discord scan failed: {exc}",
        )

    text_channels = tuple(
        LiveGuildChannel(
            channel_id=int(channel["id"]),
            name=str(channel["name"]),
            kind=f"type:{channel['type']}",
        )
        for channel in channels_payload
        if int(channel["type"]) == int(discord.ChannelType.text.value)
    )
    live_roles = tuple(
        LiveGuildRole(role_id=int(role["id"]), name=str(role["name"]))
        for role in sorted(
            guild_payload.get("roles", []),
            key=lambda item: (str(item["name"]).lower(), int(item["id"])),
        )
        if str(role["name"]) != "@everyone"
    )
    return LiveGuildScanResult(
        guild_id=guild_id,
        guild_name=guild_payload.get("name"),
        channels=tuple(sorted(text_channels, key=lambda item: (item.name, item.channel_id))),
        roles=live_roles,
        source="discord-rest",
    )


async def handle_status(ctx: OperationsContext) -> None:
    healthy = await ctx.database.health_check()
    maintenance = await ctx.maintenance.get_state()
    queue = await ctx.sends.count_statuses()
    print_header("Status")
    print_field("Database", "ok" if healthy else "failed")
    print_field("Maintenance", "on" if maintenance.enabled else "off")
    print_field(
        "Queue",
        (
            f"pending={queue.pending}, queued_maintenance={queue.queued_maintenance}, "
            f"posted={queue.posted}, failed={queue.failed}, ignored={queue.ignored}"
        ),
    )
    if maintenance.reason:
        print_field("Reason", maintenance.reason)
    print_kv("database_ok", healthy)
    print_kv("maintenance_mode", "on" if maintenance.enabled else "off")
    print_kv("maintenance_reason", maintenance.reason or "")
    print_kv_raw(
        "queue_counts="
        f"pending:{queue.pending},"
        f"queued_maintenance:{queue.queued_maintenance},"
        f"posted:{queue.posted},"
        f"failed:{queue.failed},"
        f"ignored:{queue.ignored}"
    )


async def handle_maintenance(ctx: OperationsContext, args: argparse.Namespace) -> None:
    if args.maintenance_command == "status":
        state = await ctx.maintenance.get_state()
        print_header("Maintenance")
        print_field("Mode", "on" if state.enabled else "off")
        print_field("Reason", state.reason or "(none)")
        print_kv("maintenance_mode", "on" if state.enabled else "off")
        print_kv("maintenance_reason", state.reason or "")
        return
    if args.maintenance_command == "on":
        await ctx.maintenance.enable(reason=args.reason or "")
        print_header("Maintenance")
        print_field("Mode", "on")
        print_field("Leaderboard Refresh", "requested")
        print_kv("maintenance_mode", "on")
        if args.reason:
            print_kv("maintenance_reason", args.reason)
        print_kv("leaderboard_refresh", "requested")
        return
    if args.maintenance_command == "off":
        await ctx.maintenance.disable()
        released = await ctx.sends.release_queued_maintenance()
        print_header("Maintenance")
        print_field("Mode", "off")
        print_field("Released", released)
        print_field("Leaderboard Refresh", "requested")
        print_kv("maintenance_mode", "off")
        print_kv("released", released)
        print_kv("leaderboard_refresh", "requested")
        return
    raise RuntimeError(f"Unsupported maintenance command: {args.maintenance_command}")


async def handle_queue(ctx: OperationsContext, args: argparse.Namespace) -> None:
    if args.queue_command == "status":
        queue = await ctx.sends.count_statuses()
        print_header("Queue")
        print_field("Pending", queue.pending)
        print_field("Queued Maintenance", queue.queued_maintenance)
        print_field("Posted", queue.posted)
        print_field("Failed", queue.failed)
        print_field("Ignored", queue.ignored)
        print_kv("pending", queue.pending)
        print_kv("queued_maintenance", queue.queued_maintenance)
        print_kv("posted", queue.posted)
        print_kv("failed", queue.failed)
        print_kv("ignored", queue.ignored)
        return
    if args.queue_command == "flush":
        if await ctx.maintenance.is_enabled():
            raise RuntimeError("Maintenance mode is still on. Disable it before flushing the queue.")
        released = await ctx.sends.release_queued_maintenance()
        print_header("Queue Flush")
        print_field("Released", released)
        print_kv("released", released)
        return
    raise RuntimeError(f"Unsupported queue command: {args.queue_command}")


async def handle_leaderboard(ctx: OperationsContext, args: argparse.Namespace) -> None:
    if args.leaderboard_command == "refresh":
        print_header("Leaderboard Refresh")
        print_field("Status", "requested")
        await ctx.maintenance.request_leaderboard_refresh()
        print_kv("leaderboard_refresh", "requested")
        return
    if args.leaderboard_command == "adopt":
        print_header("Leaderboard Adopt")
        print_field("Guild ID", args.guild_id)
        print_field("Channel ID", args.leaderboard_channel_id)
        print_field("Leaderboard Message ID", args.leaderboard_message_id)
        print_field("Stats Message ID", args.stats_message_id)
        await ctx.leaderboards.upsert_message(
            guild_id=args.guild_id,
            message_key="leaderboard",
            leaderboard_type="discord",
            channel_id=args.leaderboard_channel_id,
            message_id=args.leaderboard_message_id,
        )
        await ctx.leaderboards.upsert_message(
            guild_id=args.guild_id,
            message_key="leaderboard_stats",
            leaderboard_type="discord",
            channel_id=args.leaderboard_channel_id,
            message_id=args.stats_message_id,
        )
        print_kv("leaderboard_adopted", "true")
        print_kv("guild_id", args.guild_id)
        print_kv("channel_id", args.leaderboard_channel_id)
        print_kv("leaderboard_message_id", args.leaderboard_message_id)
        print_kv("stats_message_id", args.stats_message_id)
        return
    if args.leaderboard_command == "status":
        guild_id = await resolve_guild_id(ctx, getattr(args, "guild_id", None))
        summary = await ctx.leaderboards.get_summary(
            guild_id,
            include_test_sends=ctx.settings.throne_parse_test_sends_as_real_sends,
            test_gifter_usernames=ctx.settings.throne_test_gifter_usernames,
            owner_test_user_id=ctx.settings.throne_test_send_leaderboard_owner_user_id,
        )
        print_header("Leaderboard Status")
        print_field("Guild ID", guild_id)
        print_field("Registered Dom/mes", summary.domme_count)
        print_field("Tracked Sends", summary.send_count)
        print_field("Tracked Total", format_money_from_cents(summary.total_cents))
        print_kv("guild_id", guild_id)
        print_kv("registered_dommes", summary.domme_count)
        print_kv("tracked_sends", summary.send_count)
        print_kv("tracked_total_cents", summary.total_cents)
        return
    if args.leaderboard_command == "preview":
        guild_id = await resolve_guild_id(ctx, getattr(args, "guild_id", None))
        rows = await ctx.leaderboards.get_top_dommes(
            guild_id,
            limit=ctx.settings.leaderboard_limit,
            include_test_sends=ctx.settings.throne_parse_test_sends_as_real_sends,
            test_gifter_usernames=ctx.settings.throne_test_gifter_usernames,
            owner_test_user_id=ctx.settings.throne_test_send_leaderboard_owner_user_id,
        )
        print_header("Leaderboard Preview")
        print_field("Guild ID", guild_id)
        print_field("Rows", len(rows))
        print_kv("guild_id", guild_id)
        print_kv("preview", "top_dommes")
        if not rows:
            print_line("No leaderboard rows found.")
            print_kv("rows", "none")
            return
        print_section("Top Dom/mes")
        for index, row in enumerate(rows, 1):
            label = f"<@{row.user_id}>" if row.user_id else row.label
            print_line(
                f"{index}. {label} — {format_money_from_cents(row.total_cents)} across {row.send_count} send(s)"
            )
            print_kv_raw(
                f"row_{index}=user_id:{row.user_id or 0},amount_cents:{row.total_cents},send_count:{row.send_count}"
            )
        return
    if args.leaderboard_command == "diagnose":
        guild_id = await resolve_guild_id(ctx, getattr(args, "guild_id", None))
        report = await ctx.leaderboards.diagnose(
            guild_id,
            include_test_sends=ctx.settings.throne_parse_test_sends_as_real_sends,
            test_gifter_usernames=ctx.settings.throne_test_gifter_usernames,
            owner_test_user_id=ctx.settings.throne_test_send_leaderboard_owner_user_id,
            limit=ctx.settings.leaderboard_limit,
        )
        print_header("Leaderboard Diagnose")
        print_field("Guild ID", report.guild_id)
        print_field("Registered Dom/mes", report.registered_dommes)
        print_field("Counted Sends", report.counted_sends)
        print_field("Excluded Sends", report.excluded_sends)
        print_section("Excluded Reasons")
        print_line(f"- not posted: {report.excluded_not_posted}")
        print_line(f"- private: {report.excluded_private}")
        print_line(f"- test send excluded: {report.excluded_test_send}")
        print_line(f"- domme_user_id missing/mismatch: {report.excluded_domme_mismatch}")
        print_line(f"- guild mismatch: {report.excluded_guild_mismatch}")
        print_section("Dom/me Rows")
        if not report.domme_rows:
            print_line("(none)")
        for row in report.domme_rows:
            print_line(
                f"{row.label} total={format_money_from_cents(row.total_cents)} sends={row.send_count}"
            )
        print_section("Sends with No Matching Dom/me")
        if not report.unmatched_sends:
            print_line("(none)")
        for send_id, domme_user_id, send_guild_id in report.unmatched_sends:
            print_line(f"id={send_id} domme_user_id={domme_user_id} guild_id={send_guild_id}")
        print_kv("guild_id", report.guild_id)
        print_kv("registered_dommes", report.registered_dommes)
        print_kv("counted_sends", report.counted_sends)
        print_kv("excluded_sends", report.excluded_sends)
        return
    if args.leaderboard_command == "repair-send-dommes":
        guild_id = await resolve_guild_id(ctx, getattr(args, "guild_id", None))
        candidates, updated = await ctx.sends.repair_send_domme_user_ids(
            guild_id=guild_id,
            dry_run=bool(args.dry_run),
        )
        print_header("Repair Send Dom/me Links")
        print_field("Guild ID", guild_id)
        print_field("Dry Run", bool(args.dry_run))
        print_field("Candidates", candidates)
        print_field("Updated", updated)
        print_kv("guild_id", guild_id)
        print_kv("dry_run", bool(args.dry_run))
        print_kv("candidates", candidates)
        print_kv("updated", updated)
        return
    raise RuntimeError(f"Unsupported leaderboard command: {args.leaderboard_command}")


async def handle_count(ctx: OperationsContext, args: argparse.Namespace) -> None:
    guild_id = await resolve_guild_id(ctx, getattr(args, "guild_id", None))
    if args.count_command == "status":
        state = await ctx.counting.get(guild_id)
        if state is None:
            print_header("Count Status")
            print_field("Guild ID", guild_id)
            print_field("State", "missing")
            print_kv("guild_id", guild_id)
            print_kv("counting_state", "missing")
            return
        print_header("Count Status")
        print_field("Guild ID", guild_id)
        print_field("Enabled", state.is_enabled)
        print_field("Channel ID", state.channel_id or 0)
        print_field("Current Number", state.current_number)
        print_kv("guild_id", guild_id)
        print_kv("enabled", state.is_enabled)
        print_kv("channel_id", state.channel_id or 0)
        print_kv("current_number", state.current_number)
        print_kv("last_user_id", state.last_user_id or 0)
        return
    if args.count_command == "set":
        existing = await ctx.counting.get(guild_id)
        channel_id = existing.channel_id if existing is not None else None
        is_enabled = existing.is_enabled if existing is not None else channel_id is not None
        await ctx.counting.upsert(
            guild_id=guild_id,
            channel_id=channel_id,
            current_number=max(0, int(args.number)),
            last_user_id=None,
            is_enabled=is_enabled,
            pending_restore=False,
        )
        print_header("Count Set")
        print_field("Guild ID", guild_id)
        print_field("Current Number", max(0, int(args.number)))
        print_kv("guild_id", guild_id)
        print_kv("current_number", max(0, int(args.number)))
        return
    raise RuntimeError(f"Unsupported count command: {args.count_command}")


def _inactivity_enabled_key(guild_id: int) -> str:
    # Mirrors InactivityService._enabled_key.
    return f"inactivity:{guild_id}:enabled"


def _backup_enabled_key(guild_id: int) -> str:
    # Mirrors ServerBackupService._enabled_key.
    return f"server_backup:{guild_id}:enabled"


async def _post_bot_ops(path: str, payload: dict | None = None) -> tuple[dict | None, str | None]:
    """POST to the running bot's ops bridge. Returns (json, error)."""

    host = (os.getenv("ROB_OPS_HOST") or "127.0.0.1").strip()
    raw_port = (os.getenv("ROB_OPS_PORT") or "8811").strip()
    secret = (os.getenv("ROB_OPS_SECRET") or "").strip()
    try:
        port = int(raw_port)
    except ValueError:
        return None, "ROB_OPS_PORT is not a valid integer."

    headers = {"User-Agent": "RobOps/1.0 (+https://github.com/patdadev/rob)"}
    if secret:
        headers["X-Rob-Ops-Secret"] = secret

    url = f"http://{host}:{port}{path}"
    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
            async with session.post(url, json=payload or {}) as response:
                body = await response.json()
                if response.status >= 400:
                    return None, str(body.get("error") or f"HTTP {response.status}")
                return body, None
    except (aiohttp.ClientError, TimeoutError) as exc:
        return None, f"Could not reach the running bot ops bridge: {exc}"


async def handle_inactivity(ctx: OperationsContext, args: argparse.Namespace) -> None:
    guild_id = await resolve_guild_id(ctx, getattr(args, "guild_id", None))
    if args.inactivity_command in {"on", "off"}:
        enabled = args.inactivity_command == "on"
        await ctx.bot_state.set_value(_inactivity_enabled_key(guild_id), "true" if enabled else "false")
        print_header("Inactivity System")
        print_field("Guild ID", guild_id)
        print_field("Enabled", enabled)
        return
    if args.inactivity_command == "status":
        raw = await ctx.bot_state.get_text(_inactivity_enabled_key(guild_id))
        enabled = (raw or "").strip().lower() in {"1", "true", "yes", "on"}
        bootstrapped = await ctx.bot_state.get_text(f"inactivity:{guild_id}:bootstrapped_at")
        watchlist = await ctx.inactive_users.list_for_guild(guild_id)
        settings = await ctx.guild_settings.get(guild_id)
        print_header("Inactivity System")
        print_field("Guild ID", guild_id)
        print_field("Enabled", enabled if raw is not None else "(default off)")
        print_field("Active Role", getattr(settings, "active_role_id", None) or "(not set)")
        print_field("Inactive Role", getattr(settings, "inactive_role_id", None) or "(not set)")
        print_field("Unverified Role", getattr(settings, "unverified_role_id", None) or "(not set)")
        print_field("Bootstrapped", bootstrapped or "(not yet)")
        print_field("On Watchlist", len(watchlist))
        return
    raise RuntimeError(f"Unsupported inactivity command: {args.inactivity_command}")


async def handle_backup(ctx: OperationsContext, args: argparse.Namespace) -> None:
    guild_id = await resolve_guild_id(ctx, getattr(args, "guild_id", None))
    if args.backup_command in {"on", "off"}:
        enabled = args.backup_command == "on"
        await ctx.bot_state.set_value(_backup_enabled_key(guild_id), "true" if enabled else "false")
        print_header("Server Backup System")
        print_field("Guild ID", guild_id)
        print_field("Enabled", enabled)
        return
    if args.backup_command == "status":
        raw = await ctx.bot_state.get_text(_backup_enabled_key(guild_id))
        enabled = (raw or "").strip().lower() in {"1", "true", "yes", "on"}
        latest = await ctx.server_backups.get_latest_backup(guild_id)
        pending = await ctx.server_backups.get_pending_approval(guild_id)
        total = await ctx.server_backups.count_backups(guild_id)
        settings = await ctx.guild_settings.get(guild_id)
        print_header("Server Backup System")
        print_field("Guild ID", guild_id)
        print_field("Enabled", enabled if raw is not None else "(default off)")
        print_field("Approval Channel", getattr(settings, "backup_approval_channel_id", None) or "(not set)")
        print_field("Stored Backups", total)
        print_field("Last Backup", format_optional_datetime(latest.created_at) if latest is not None else "(none)")
        if pending is not None:
            print_field(
                "Pending Approval",
                f"id={pending.id}, {len(pending.approved_by)}/{pending.required_approvals} approvals, "
                f"{len(pending.changes)} major change(s)",
            )
        else:
            print_field("Pending Approval", "(none)")
        return
    if args.backup_command == "run":
        body, error = await _post_bot_ops(f"/guilds/{guild_id}/backup/run")
        print_header("Server Backup Run")
        print_field("Guild ID", guild_id)
        if error is not None:
            print_field("Status", "failed")
            print_field("Error", error)
            return
        print_field("Action", body.get("action"))
        print_field("Changes", f"{body.get('change_count', 0)} ({body.get('major_change_count', 0)} major)")
        for detail in body.get("major_changes", []):
            print_line(f"  * {detail}")
        return
    raise RuntimeError(f"Unsupported backup command: {args.backup_command}")


async def handle_throne(ctx: OperationsContext, args: argparse.Namespace) -> None:
    if args.throne_command == "refresh":
        print_header("Throne Refresh")
        print_field("Tracking Mode", "webhook_only")
        print_field("Legacy Polling", "disabled")
        print_line("Use Throne webhook integrations and run a test webhook.")
        print_kv("tracking_mode", "webhook_only")
        print_kv("legacy_polling", "disabled")
        print_kv("action", "Use Throne webhook integrations and run a test webhook.")
        return

    if args.throne_command == "status":
        guild_id = await resolve_guild_id(ctx, getattr(args, "guild_id", None))
        handle = (getattr(args, "handle", None) or "").strip()
        if handle:
            creator = await ctx.throne_creators.get_by_handle(guild_id, handle)
            if creator is None:
                print_header("Throne Status")
                print_field("Guild ID", guild_id)
                print_field("Handle", f"@{handle}")
                print_field("Found", "false")
                print_kv("guild_id", guild_id)
                print_kv("handle", f"@{handle}")
                print_kv("found", "false")
                return
            print_header("Throne Status")
            print_field("Guild ID", guild_id)
            print_field("Handle", f"@{creator.throne_handle}")
            print_field("Found", "true")
            print_field("Creator ID", creator.throne_creator_id)
            print_field("Discord User ID", creator.discord_user_id)
            print_field("Tracking Mode", creator.tracking_mode)
            print_field("Webhook Connected", format_optional_datetime(creator.webhook_connected_at))
            print_field("Last Successful Event", format_optional_datetime(creator.last_successful_event_at))
            print_field("Setup Verified", format_optional_datetime(creator.setup_verified_at))
            print_kv("guild_id", guild_id)
            print_kv("handle", f"@{creator.throne_handle}")
            print_kv("found", "true")
            print_kv("creator_id", creator.throne_creator_id)
            print_kv("discord_user_id", creator.discord_user_id)
            print_kv("tracking_mode", creator.tracking_mode)
            print_kv(
                "webhook_connected_at",
                creator.webhook_connected_at.isoformat() if creator.webhook_connected_at else "",
            )
            print_kv(
                "last_successful_event_at",
                creator.last_successful_event_at.isoformat() if creator.last_successful_event_at else "",
            )
            print_kv(
                "setup_verified_at",
                creator.setup_verified_at.isoformat() if creator.setup_verified_at else "",
            )
            return

        creators = await ctx.throne_creators.list_for_guild(guild_id)
        setup_verified = sum(1 for row in creators if row.setup_verified_at is not None)
        print_header("Throne Status")
        print_field("Guild ID", guild_id)
        print_field("Registered Creators", len(creators))
        print_field("Setup Verified", setup_verified)
        print_kv("guild_id", guild_id)
        print_kv("registered_creators", len(creators))
        print_kv("setup_verified", setup_verified)
        return

    if args.throne_command in {"dommes", "list"}:
        guild_id = await resolve_guild_id(ctx, getattr(args, "guild_id", None))
        creators = await ctx.throne_creators.list_for_guild(guild_id)
        print_header("Throne Dom/mes")
        print_field("Guild ID", guild_id)
        print_field("Rows", len(creators))
        print_kv("guild_id", guild_id)
        print_kv("rows", len(creators))
        if not creators:
            print_line("No registered Throne creators found.")
            return
        print_section("Creators")
        for index, row in enumerate(creators, 1):
            print_line(
                f"{index}. @{row.throne_handle} — Creator ID: {row.throne_creator_id} | "
                f"User ID: {row.discord_user_id} | Mode: {row.tracking_mode}"
            )
            print_kv_raw(
                f"handle=@{row.throne_handle} creator_id={row.throne_creator_id} "
                f"discord_user_id={row.discord_user_id} mode={row.tracking_mode} "
                f"last_successful_event_at={row.last_successful_event_at.isoformat() if row.last_successful_event_at else ''}"
            )
            print_kv_raw(
                f"creator_{index}=handle:@{row.throne_handle},creator_id:{row.throne_creator_id},"
                f"discord_user_id:{row.discord_user_id},mode:{row.tracking_mode},"
                f"last_successful_event_at:{row.last_successful_event_at.isoformat() if row.last_successful_event_at else ''}"
            )
        return

    if args.throne_command == "search":
        guild_id = await resolve_guild_id(ctx, getattr(args, "guild_id", None))
        user_id = parse_user_ref(str(args.user_ref))
        if user_id is None:
            raise RuntimeError("Could not parse user ref. Use a mention like <@123> or raw Discord user ID.")
        creator = await ctx.throne_creators.get_by_user_id(guild_id, user_id)
        if creator is None:
            print_header("Throne Search")
            print_field("Guild ID", guild_id)
            print_field("User ID", user_id)
            print_field("Found", "false")
            print_kv("guild_id", guild_id)
            print_kv("user_id", user_id)
            print_kv("found", "false")
            return
        latest_sends = await ctx.sends.list_sends_for_domme(guild_id, user_id, limit=1)
        latest = latest_sends[0] if latest_sends else None
        print_header("Throne Search")
        print_field("Guild ID", guild_id)
        print_field("User ID", user_id)
        print_field("Found", "true")
        print_field("Creator ID", creator.throne_creator_id)
        print_field("Handle", f"@{creator.throne_handle}")
        print_field("Tracking Mode", creator.tracking_mode)
        print_kv("guild_id", guild_id)
        print_kv("user_id", user_id)
        print_kv("found", "true")
        print_kv("handle", f"@{creator.throne_handle}")
        print_kv("creator_id", creator.throne_creator_id)
        print_kv("tracking_mode", creator.tracking_mode)
        print_kv(
            "webhook_connected_at",
            creator.webhook_connected_at.isoformat() if creator.webhook_connected_at else "",
        )
        print_kv(
            "last_successful_event_at",
            creator.last_successful_event_at.isoformat() if creator.last_successful_event_at else "",
        )
        print_kv(
            "setup_verified_at",
            creator.setup_verified_at.isoformat() if creator.setup_verified_at else "",
        )
        if latest is not None:
            print_field("Latest Send", format_money_from_cents(latest.amount_cents))
            print_kv("latest_send_id", latest.id)
            print_kv("latest_send_amount_cents", latest.amount_cents)
            print_kv("latest_send_sub_name", latest.sub_name or "")
            print_kv("latest_send_at", latest.sent_at.isoformat())
        else:
            print_field("Latest Send", "(none)")
            print_kv("latest_send_id", 0)
        return

    if args.throne_command == "webhook":
        command = getattr(args, "throne_webhook_command", None)
        if command != "refresh":
            raise RuntimeError(f"Unsupported throne webhook command: {command}")
        guild_id = await resolve_guild_id(ctx, getattr(args, "guild_id", None))
        user_id = parse_user_ref(str(args.user_ref))
        if user_id is None:
            raise RuntimeError("Could not parse user ref. Use a mention like <@123> or raw Discord user ID.")
        creator = await ctx.throne_creators.get_by_user_id(guild_id, user_id)
        if creator is None:
            print_header("Throne Webhook Refresh")
            print_field("Guild ID", guild_id)
            print_field("User ID", user_id)
            print_field("Found", "false")
            print_kv("guild_id", guild_id)
            print_kv("user_id", user_id)
            print_kv("found", "false")
            return
        new_secret = secrets.token_urlsafe(32)
        updated = await ctx.throne_creators.upsert_for_user(
            guild_id=creator.guild_id,
            domme_id=creator.domme_id,
            discord_user_id=creator.discord_user_id,
            throne_handle=creator.throne_handle,
            throne_creator_id=creator.throne_creator_id,
            hide_own_purchases=creator.hide_own_purchases,
            tracking_mode=creator.tracking_mode,
            webhook_secret=new_secret,
            webhook_secret_hash=hash_webhook_secret(new_secret),
        )
        print_header("Throne Webhook Refresh")
        print_field("Guild ID", guild_id)
        print_field("User ID", user_id)
        print_field("Rotated", "true")
        print_kv("guild_id", guild_id)
        print_kv("user_id", user_id)
        print_kv("found", "true")
        print_kv("rotated", "true")
        print_kv("creator_id", updated.throne_creator_id)
        base = (os.getenv("THRONE_WEBHOOK_BASE_URL") or "").strip().rstrip("/")
        if base:
            webhook_url = f"{base}/throne/webhook/{updated.throne_creator_id}/{new_secret}"
            print_field("Webhook URL", webhook_url)
            print_kv("webhook_url", webhook_url)
        else:
            print_field("Webhook URL", "(not configured)")
            print_kv("webhook_url", "")
        return

    if args.throne_command == "addsend":
        guild_id = await resolve_guild_id(ctx, getattr(args, "guild_id", None))
        user_id = parse_user_ref(str(args.user_ref))
        if user_id is None:
            raise RuntimeError("Could not parse user ref. Use a mention like <@123> or raw Discord user ID.")
        if float(args.amount) <= 0:
            raise RuntimeError("Amount must be greater than zero.")
        domme = await ctx.dommes.get_by_user_id(guild_id, user_id)
        send = await ctx.send_service.record_manual_send(
            guild_id=guild_id,
            domme_id=domme.id if domme is not None else None,
            domme_user_id=user_id,
            amount_cents=dollars_to_cents(float(args.amount)),
            currency=(args.currency or "USD").strip().upper(),
            method=(args.method or "manual").strip() or "manual",
            note=(args.note or "").strip() or None,
            sub_name=(args.sub_name or "").strip() or None,
            source="manual:robctl_throne_addsend",
        )
        if send is None:
            print_header("Throne Add Send")
            print_field("Recorded", "false")
            print_kv("recorded", "false")
            return
        print_header("Throne Add Send")
        print_field("Recorded", "true")
        print_field("Public Send ID", send.public_send_id)
        print_field("Status", send.discord_post_status)
        print_kv("recorded", "true")
        print_kv("send_id", send.id)
        print_kv("public_send_id", send.public_send_id)
        print_kv("status", send.discord_post_status)
        return

    if args.throne_command == "addsub":
        guild_id = await resolve_guild_id(ctx, getattr(args, "guild_id", None))
        user_id = parse_user_ref(str(args.user_ref))
        if user_id is None:
            raise RuntimeError("Could not parse user ref. Use a mention like <@123> or raw Discord user ID.")
        cleaned_name = " ".join(str(args.name).strip().split())
        if not cleaned_name:
            raise RuntimeError("A Sub sending name is required.")
        sub = await ctx.subs.upsert(
            guild_id=guild_id,
            discord_user_id=user_id,
            send_name=cleaned_name,
        )
        print_header("Throne Add Sub")
        print_field("Guild ID", guild_id)
        print_field("User ID", sub.discord_user_id)
        print_field("Send Name", sub.send_name)
        print_kv("guild_id", guild_id)
        print_kv("user_id", sub.discord_user_id)
        print_kv("sub_id", sub.id)
        print_kv("send_name", sub.send_name)
        return

    if args.throne_command == "adddomme":
        guild_id = await resolve_guild_id(ctx, getattr(args, "guild_id", None))
        user_id = parse_user_ref(str(args.user_ref))
        if user_id is None:
            raise RuntimeError("Could not parse user ref. Use a mention like <@123> or raw Discord user ID.")
        result = await ctx.registration_service.register_domme(
            guild_id=guild_id,
            discord_user_id=user_id,
            throne_input=str(args.throne_input),
        )
        print_header("Throne Add Dom/me")
        print_field("Guild ID", guild_id)
        print_field("User ID", user_id)
        print_field("Creator ID", result.creator.throne_creator_id)
        print_field("Handle", f"@{result.creator.throne_handle}")
        print_field("Tracking Mode", result.creator.tracking_mode)
        if result.webhook_url:
            print_field("Webhook URL", result.webhook_url)
        print_kv("guild_id", guild_id)
        print_kv("user_id", user_id)
        print_kv("domme_id", result.domme.id)
        print_kv("throne_handle", f"@{result.creator.throne_handle}")
        print_kv("creator_id", result.creator.throne_creator_id)
        print_kv("tracking_mode", result.creator.tracking_mode)
        print_kv("webhook_url", result.webhook_url or "")
        return

    if args.throne_command == "subs":
        guild_id = await resolve_guild_id(ctx, getattr(args, "guild_id", None))
        subs = await ctx.subs.list_for_guild(guild_id)
        print_header("Throne Subs")
        print_field("Guild ID", guild_id)
        print_field("Rows", len(subs))
        print_kv("guild_id", guild_id)
        print_kv("rows", len(subs))
        if not subs:
            print_line("No registered Subs found.")
            return
        print_section("Subs")
        for index, row in enumerate(subs, 1):
            print_line(
                f"{index}. {row.send_name} — User ID: {row.discord_user_id} | "
                f"Registered: {row.registered_at.isoformat()}"
            )
            print_kv_raw(
                f"sub_{index}=discord_user_id:{row.discord_user_id},send_name:{row.send_name},"
                f"registered_at:{row.registered_at.isoformat()}"
            )
        return
    if args.throne_command == "invalidate-test-sends":
        usernames = list(ctx.settings.throne_test_gifter_usernames)
        updated = await ctx.sends.mark_known_test_sends(test_gifter_usernames=usernames)
        print_header("Invalidate Test Sends")
        print_field("Updated", updated)
        print_field("Usernames", ",".join(usernames))
        print_kv("updated", updated)
        print_kv("usernames", ",".join(usernames))
        return
    raise RuntimeError(f"Unsupported throne command: {args.throne_command}")


async def handle_blacklist(ctx: OperationsContext, args: argparse.Namespace) -> None:
    if args.blacklist_command == "add":
        await ctx.blacklist.add(
            discord_user_id=int(args.discord_user_id),
            reason=(args.reason or "manual").strip() or "manual",
            created_by=args.created_by,
        )
        print_header("Blacklist Updated")
        print_field("Action", "added")
        print_field("Discord User ID", int(args.discord_user_id))
        print_kv("discord_user_id", int(args.discord_user_id))
        print_kv("updated", 1)
        return
    if args.blacklist_command == "remove":
        await ctx.blacklist.remove(int(args.discord_user_id))
        print_header("Blacklist Updated")
        print_field("Action", "removed")
        print_field("Discord User ID", int(args.discord_user_id))
        print_kv("discord_user_id", int(args.discord_user_id))
        print_kv("updated", 1)
        return
    if args.blacklist_command == "list":
        rows = await ctx.blacklist.list_entries(limit=max(1, int(args.limit)))
        print_header("Blacklist")
        print_field("Rows", len(rows))
        print_kv("rows", len(rows))
        if not rows:
            print_line("No blacklist entries found.")
            return
        print_section("Entries")
        for index, (user_id, reason, created_by, created_at) in enumerate(rows, 1):
            print_line(f"{index}. user_id={user_id} reason={reason or '(none)'}")
            print_kv_raw(
                f"entry_{index}=discord_user_id:{user_id},reason:{reason or ''},"
                f"created_by:{created_by or 0},created_at:{created_at.isoformat()}"
            )
        return
    raise RuntimeError(f"Unsupported blacklist command: {args.blacklist_command}")


async def handle_sends(ctx: OperationsContext, args: argparse.Namespace) -> None:
    if args.sends_command == "list":
        guild_id = getattr(args, "guild_id", None)
        if guild_id is None:
            try:
                guild_id = await resolve_guild_id(ctx, None)
            except RuntimeError:
                guild_id = None
        rows = await ctx.sends.list_sends(
            guild_id=guild_id,
            status=args.status,
            limit=max(1, int(args.limit)),
        )
        print_header("Sends List")
        print_field("Status", args.status)
        print_field("Guild ID", guild_id if guild_id is not None else "all")
        print_field("Rows", len(rows))
        print_kv("status", args.status)
        print_kv("guild_id", guild_id if guild_id is not None else "all")
        print_kv("rows", len(rows))
        if not rows:
            print_line("No sends matched the current filter.")
            return
        print_section("Sends")
        for index, send in enumerate(rows, 1):
            print_line(
                f"{index}. {send.public_send_id} — {format_money_with_currency_name(send.amount_cents, send.currency)} "
                f"status={send.discord_post_status} domme_user_id={send.domme_user_id}"
            )
            print_kv_raw(
                f"send_{index}=id:{send.id},guild_id:{send.guild_id},domme_user_id:{send.domme_user_id},"
                f"sub_user_id:{send.sub_user_id or 0},amount_cents:{send.amount_cents},status:{send.discord_post_status},"
                f"is_private:{send.is_private},is_test_send:{send.is_test_send}"
            )
        return
    if args.sends_command == "backfill-public-ids":
        updated = await ctx.sends.backfill_public_send_ids()
        print_header("Backfill Public Send IDs")
        print_field("Updated", updated)
        print_kv("updated", updated)
        return
    if args.sends_command == "repair-mentions":
        guild_id = getattr(args, "guild_id", None)
        candidates, updated = await ctx.sends.repair_mention_sub_links(
            guild_id=guild_id,
            dry_run=bool(args.dry_run),
        )
        print_header("Repair Mention Sub Links")
        print_field("Guild ID", guild_id if guild_id is not None else "all")
        print_field("Dry Run", bool(args.dry_run))
        print_field("Candidates", candidates)
        print_field("Updated", updated)
        print_kv("guild_id", guild_id if guild_id is not None else "all")
        print_kv("dry_run", bool(args.dry_run))
        print_kv("candidates", candidates)
        print_kv("updated", updated)
        return
    if args.sends_command == "mark-posted":
        updated = await ctx.sends.force_mark_posted(args.send_id)
        print_header("Mark Send Posted")
        print_field("Send ID", args.send_id)
        print_field("Updated", updated)
        print_kv("send_id", args.send_id)
        print_kv("updated", updated)
        return
    raise RuntimeError(f"Unsupported sends command: {args.sends_command}")


async def handle_guild(ctx: OperationsContext, args: argparse.Namespace) -> None:
    guild_id = int(args.guild_id)
    if args.guild_command == "scan":
        settings = await ctx.guild_settings.get(guild_id)
        live = await fetch_live_guild_scan(guild_id)

        print_header("Guild Scan")
        print_field("Guild ID", guild_id)
        print_field("Guild Name", live.guild_name or "(unknown)")
        print_field("Live Text Channels", len(live.channels))
        print_field("Live Roles", len(live.roles))
        print_field("Live Source", live.source)
        print_kv("guild_id", guild_id)
        print_kv("guild_name", live.guild_name or "")
        print_kv("live_text_channels", len(live.channels))
        print_kv("live_roles", len(live.roles))
        print_kv("live_source", live.source)

        if live.error:
            print_field("Live Scan", live.error)
            print_kv("live_scan_error", live.error)

        configured_channel_ids = {
            field_name: getattr(settings, field_name, None) if settings is not None else None
            for field_name in GUILD_CHANNEL_FIELDS
        }
        channel_lookup = {channel.channel_id: channel for channel in live.channels}
        print_section("Channels")

        for field_name in GUILD_CHANNEL_FIELDS:
            label = GUILD_CHANNEL_LABELS[field_name]
            configured_id = configured_channel_ids[field_name]
            configured_channel = channel_lookup.get(configured_id) if configured_id is not None else None
            suggested_channel = _find_best_channel_match(live.channels, field_name)

            status = "missing"
            if configured_id is not None and configured_channel is not None:
                status = "configured"
            elif configured_id is not None:
                status = "configured_missing"

            print_kv(field_name, configured_id or "")
            print_kv(f"{field_name}_status", status)
            if suggested_channel is not None:
                print_kv(f"{field_name}_suggested", suggested_channel.channel_id)

            print_line(f"{label}:")
            if configured_id is None:
                print_line("  current: (not set)")
            elif configured_channel is None:
                print_line(f"  current: {configured_id} (not found in live guild scan)")
            else:
                print_line(f"  current: #{configured_channel.name} ({configured_channel.channel_id})")

            if suggested_channel is None:
                print_line("  suggested: (no obvious match found)")
            else:
                print_line(f"  suggested: #{suggested_channel.name} ({suggested_channel.channel_id})")
                if configured_id != suggested_channel.channel_id or configured_channel is None:
                    print_line(
                        "  command: "
                        f"rob guild set-channel --guild-id {guild_id} --field {field_name} --channel-id {suggested_channel.channel_id}"
                    )

        configured_role_ids = {
            field_name: getattr(settings, field_name, None) if settings is not None else None
            for field_name in GUILD_ROLE_FIELDS
        }
        role_lookup = {role.role_id: role for role in live.roles}
        print_section("Roles")

        for field_name in GUILD_ROLE_FIELDS:
            label = GUILD_ROLE_LABELS[field_name]
            configured_id = configured_role_ids[field_name]
            configured_role = role_lookup.get(configured_id) if configured_id is not None else None
            suggested_role = _find_best_role_match(live.roles, field_name)

            status = "missing"
            if configured_id is not None and configured_role is not None:
                status = "configured"
            elif configured_id is not None:
                status = "configured_missing"

            print_kv(field_name, configured_id or "")
            print_kv(f"{field_name}_status", status)
            if suggested_role is not None:
                print_kv(f"{field_name}_suggested", suggested_role.role_id)

            print_line(f"{label}:")
            if configured_id is None:
                print_line("  current: (not set)")
            elif configured_role is None:
                print_line(f"  current: {configured_id} (not found in live guild scan)")
            else:
                print_line(f"  current: @{configured_role.name} ({configured_role.role_id})")

            if suggested_role is None:
                print_line("  suggested: (no obvious match found)")
            else:
                print_line(f"  suggested: @{suggested_role.name} ({suggested_role.role_id})")
                if configured_id != suggested_role.role_id or configured_role is None:
                    print_line(
                        "  command: "
                        f"rob guild set-role --guild-id {guild_id} --field {field_name} --role-id {suggested_role.role_id}"
                    )
        return

    if args.guild_command == "set-channel":
        channel_id = None if getattr(args, "clear", False) else int(args.channel_id)
        updated = await ctx.guild_settings.set_channel_id(guild_id, args.field, channel_id)
        print_header("Guild Channel Updated")
        print_field("Guild ID", guild_id)
        print_field("Field", args.field)
        print_field("Channel ID", channel_id if channel_id is not None else "(cleared)")
        print_kv("updated", "true")
        print_kv("guild_id", guild_id)
        print_kv("field", args.field)
        print_kv("channel_id", getattr(updated, args.field) or 0)
        return

    if args.guild_command == "set-role":
        role_id = None if getattr(args, "clear", False) else int(args.role_id)
        updated = await ctx.guild_settings.set_role_id(guild_id, args.field, role_id)
        print_header("Guild Role Updated")
        print_field("Guild ID", guild_id)
        print_field("Field", args.field)
        print_field("Role ID", role_id if role_id is not None else "(cleared)")
        print_kv("updated", "true")
        print_kv("guild_id", guild_id)
        print_kv("field", args.field)
        print_kv("role_id", getattr(updated, args.field) or 0)
        return

    raise RuntimeError(f"Unsupported guild command: {args.guild_command}")


async def main_async() -> None:
    parser = build_parser()
    args = parser.parse_args()
    ctx = await create_context()
    try:
        if args.command == "status":
            await handle_status(ctx)
        elif args.command == "maintenance":
            await handle_maintenance(ctx, args)
        elif args.command == "queue":
            await handle_queue(ctx, args)
        elif args.command == "leaderboard":
            await handle_leaderboard(ctx, args)
        elif args.command == "count":
            await handle_count(ctx, args)
        elif args.command == "inactivity":
            await handle_inactivity(ctx, args)
        elif args.command == "backup":
            await handle_backup(ctx, args)
        elif args.command == "throne":
            await handle_throne(ctx, args)
        elif args.command == "blacklist":
            await handle_blacklist(ctx, args)
        elif args.command == "sends":
            await handle_sends(ctx, args)
        elif args.command == "guild":
            await handle_guild(ctx, args)
        else:
            raise RuntimeError(f"Unsupported command: {args.command}")
    finally:
        await ctx.throne_service.close()
        await ctx.database.close()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
