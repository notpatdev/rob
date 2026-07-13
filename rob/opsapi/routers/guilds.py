from __future__ import annotations

import logging
from typing import Any

import discord
from aiohttp import web

from rob.opsapi._support import (
    GUILD_CHANNEL_FIELDS,
    GUILD_CHANNEL_LABELS,
    GUILD_ROLE_FIELDS,
    GUILD_ROLE_LABELS,
    SCAN_APPLY_FIELD_ORDER,
    _find_best_channel_match,
    _find_best_role_match,
)

log = logging.getLogger(__name__)


class GuildRoutes:

    async def _handle_guild_scan(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)

        guild_id = self._match_guild_id(request)
        if guild_id is None:
            return web.json_response({"error": "invalid_guild_id"}, status=400)
        payload, status = await self._build_guild_scan_payload(guild_id)
        if self._wants_text(request):
            return web.Response(
                text=self._format_guild_scan_text(payload),
                status=status,
                content_type="text/plain",
            )
        return web.json_response(payload, status=status)

    async def _handle_apply_guild_scan(self, request: web.Request) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)
        if not hasattr(self.bot, "vib_settings_repo"):
            return web.json_response({"error": "vib_settings_repo_unavailable"}, status=500)

        guild_id = self._match_guild_id(request)
        if guild_id is None:
            return web.json_response({"error": "invalid_guild_id"}, status=400)

        payload = await self._json_payload(request)
        selected_fields, invalid_options = self._parse_scan_apply_options(payload.get("options"))
        if invalid_options:
            error_payload = {
                "error": "invalid_options",
                "invalid_options": invalid_options,
                "valid_options": ["all", "channels", "roles", *SCAN_APPLY_FIELD_ORDER],
            }
            if self._wants_text(request):
                return web.Response(
                    text=self._format_invalid_scan_options_text(error_payload),
                    status=400,
                    content_type="text/plain",
                )
            return web.json_response(error_payload, status=400)

        scan_payload, status = await self._build_guild_scan_payload(guild_id)
        if status != 200:
            if self._wants_text(request):
                return web.Response(
                    text=self._format_guild_scan_text(scan_payload),
                    status=status,
                    content_type="text/plain",
                )
            return web.json_response(scan_payload, status=status)

        applied: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for entry in [*scan_payload["channel_matches"], *scan_payload["role_matches"]]:
            if entry["field"] not in selected_fields:
                continue

            current = entry["current"]
            suggested = entry["suggested"]
            if suggested is None:
                skipped.append(
                    {
                        "field": entry["field"],
                        "label": entry["label"],
                        "reason": "no_suggestion",
                    }
                )
                continue
            if current["id"] == suggested["id"] and current.get("found", True):
                skipped.append(
                    {
                        "field": entry["field"],
                        "label": entry["label"],
                        "reason": "already_matches",
                        "target_id": suggested["id"],
                        "target_name": suggested["name"],
                    }
                )
                continue

            if entry["type"] == "channel":
                await self.bot.vib_settings_repo.set_channel_id(
                    guild_id,
                    entry["field"],
                    int(suggested["id"]),
                )
            else:
                await self.bot.vib_settings_repo.set_role_id(
                    guild_id,
                    entry["field"],
                    int(suggested["id"]),
                )
            applied.append(
                {
                    "field": entry["field"],
                    "label": entry["label"],
                    "target_type": entry["type"],
                    "target_id": int(suggested["id"]),
                    "target_name": str(suggested["name"]),
                }
            )

        if applied:
            await self._refresh_guild(guild_id)

        result_payload = {
            "ok": True,
            "guild_id": guild_id,
            "guild_name": scan_payload.get("guild_name"),
            "selected_fields": [field for field in SCAN_APPLY_FIELD_ORDER if field in selected_fields],
            "applied": applied,
            "skipped": skipped,
        }
        if self._wants_text(request):
            return web.Response(
                text=self._format_guild_apply_text(result_payload),
                content_type="text/plain",
            )
        return web.json_response(result_payload)

    async def _handle_set_guild_channel(self, request: web.Request) -> web.Response:
        return await self._handle_set_guild_field(request, kind="channel")

    async def _handle_set_guild_role(self, request: web.Request) -> web.Response:
        return await self._handle_set_guild_field(request, kind="role")

    async def _handle_set_guild_field(self, request: web.Request, *, kind: str) -> web.Response:
        if not self._is_authorized(request):
            return web.json_response({"error": "forbidden"}, status=403)
        if not hasattr(self.bot, "vib_settings_repo"):
            return web.json_response({"error": "vib_settings_repo_unavailable"}, status=500)
        guild_id = self._match_guild_id(request)
        if guild_id is None:
            return web.json_response({"error": "invalid_guild_id"}, status=400)

        valid_fields = GUILD_CHANNEL_FIELDS if kind == "channel" else GUILD_ROLE_FIELDS
        id_key = "channel_id" if kind == "channel" else "role_id"
        payload = await self._json_payload(request)
        field = str(payload.get("field") or "").strip()
        if field not in valid_fields:
            return web.json_response(
                {"error": "invalid_field", "valid_fields": list(valid_fields)},
                status=400,
            )

        clear = self._payload_bool(payload, "clear")
        target_id: int | None = None
        if not clear:
            raw = payload.get(id_key)
            try:
                target_id = int(raw)
            except (TypeError, ValueError):
                return web.json_response({"error": f"invalid_{id_key}"}, status=400)

        if kind == "channel":
            updated = await self.bot.vib_settings_repo.set_channel_id(guild_id, field, target_id)
        else:
            updated = await self.bot.vib_settings_repo.set_role_id(guild_id, field, target_id)
        await self._refresh_guild(guild_id)

        stored = getattr(updated, field, None)
        result = {
            "ok": True,
            "guild_id": guild_id,
            "field": field,
            id_key: stored,
        }
        if self._wants_text(request):
            label = "Guild Channel Updated" if kind == "channel" else "Guild Role Updated"
            text = "\n".join(
                [
                    f"Rob Control | {label}",
                    f"- Guild ID: {guild_id}",
                    f"- Field: {field}",
                    f"- {id_key}: {stored if stored is not None else '(cleared)'}",
                ]
            )
            return web.Response(text=text + "\n", content_type="text/plain")
        return web.json_response(result)

    async def _build_guild_scan_payload(self, guild_id: int) -> tuple[dict[str, Any], int]:
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return (
                {
                    "guild_id": guild_id,
                    "guild_name": None,
                    "channels": [],
                    "roles": [],
                    "channel_matches": [],
                    "role_matches": [],
                    "source": "bot-session",
                    "error": "Guild is not currently available in the running bot cache.",
                },
                404,
            )

        settings = None
        if hasattr(self.bot, "vib_settings_repo"):
            settings = await self.bot.vib_settings_repo.get(guild_id)

        channels = [
            {
                "id": int(channel.id),
                "name": str(channel.name),
                "kind": type(channel).__name__,
            }
            for channel in sorted(guild.channels, key=lambda item: (item.name.lower(), item.id))
            if isinstance(channel, discord.TextChannel)
        ]
        roles = [
            {
                "id": int(role.id),
                "name": str(role.name),
            }
            for role in sorted(guild.roles, key=lambda item: (item.name.lower(), item.id))
            if role.name != "@everyone"
        ]

        channel_lookup = {int(channel["id"]): channel for channel in channels}
        role_lookup = {int(role["id"]): role for role in roles}

        channel_matches: list[dict[str, Any]] = []
        for field_name in GUILD_CHANNEL_FIELDS:
            configured_id = getattr(settings, field_name, None) if settings is not None else None
            current = channel_lookup.get(configured_id) if configured_id is not None else None
            suggested = _find_best_channel_match(channels, field_name)
            channel_matches.append(
                {
                    "type": "channel",
                    "field": field_name,
                    "label": GUILD_CHANNEL_LABELS[field_name],
                    "current": {
                        "id": configured_id,
                        "name": current["name"] if current is not None else None,
                        "kind": current["kind"] if current is not None else None,
                        "found": current is not None,
                    },
                    "suggested": suggested,
                }
            )

        role_matches: list[dict[str, Any]] = []
        for field_name in GUILD_ROLE_FIELDS:
            configured_id = getattr(settings, field_name, None) if settings is not None else None
            current = role_lookup.get(configured_id) if configured_id is not None else None
            suggested = _find_best_role_match(roles, field_name)
            role_matches.append(
                {
                    "type": "role",
                    "field": field_name,
                    "label": GUILD_ROLE_LABELS[field_name],
                    "current": {
                        "id": configured_id,
                        "name": current["name"] if current is not None else None,
                        "found": current is not None,
                    },
                    "suggested": suggested,
                }
            )

        return (
            {
                "guild_id": int(guild.id),
                "guild_name": guild.name,
                "channels": channels,
                "roles": roles,
                "channel_matches": channel_matches,
                "role_matches": role_matches,
                "source": "bot-session",
            },
            200,
        )

    @staticmethod
    def _parse_scan_apply_options(raw: Any) -> tuple[set[str], list[str]]:
        if raw is None:
            return set(SCAN_APPLY_FIELD_ORDER), []

        if isinstance(raw, list):
            parts = ",".join(str(item) for item in raw)
        else:
            parts = str(raw)
        if not parts.strip():
            return set(SCAN_APPLY_FIELD_ORDER), []

        selected: set[str] = set()
        invalid: list[str] = []
        for raw_token in parts.split(","):
            token = raw_token.strip()
            if not token:
                continue
            normalized = token.casefold()
            if normalized == "all":
                return set(SCAN_APPLY_FIELD_ORDER), []
            if normalized == "channels":
                selected.update(GUILD_CHANNEL_FIELDS)
                continue
            if normalized == "roles":
                selected.update(GUILD_ROLE_FIELDS)
                continue
            if normalized in SCAN_APPLY_FIELD_ORDER:
                selected.add(normalized)
                continue
            invalid.append(token)

        if not selected and not invalid:
            selected.update(SCAN_APPLY_FIELD_ORDER)
        return selected, invalid

    @staticmethod
    def _format_guild_scan_text(payload: dict[str, Any]) -> str:
        lines = [
            "Guild Scan",
            f"Guild ID: {payload['guild_id']}",
            f"Guild Name: {payload.get('guild_name') or '(unknown)'}",
            f"Live Text Channels: {len(payload.get('channels', []))}",
            f"Live Roles: {len(payload.get('roles', []))}",
            f"Live Source: {payload.get('source', 'bot-session')}",
        ]
        if payload.get("error"):
            lines.append(f"Live Scan: {payload['error']}")

        channel_matches = payload.get("channel_matches") or []
        if channel_matches:
            lines.extend(["", "Channels"])
            for entry in channel_matches:
                lines.append(f"{entry['label']}:")
                lines.append(f"  current: {GuildRoutes._format_scan_current(entry)}")
                lines.append(f"  suggested: {GuildRoutes._format_scan_suggested(entry)}")
                suggested = entry.get("suggested")
                current = entry.get("current") or {}
                if suggested is not None and (
                    current.get("id") != suggested.get("id") or not current.get("found", True)
                ):
                    lines.append(
                        "  auto-apply: "
                        f"rob auto-apply --guild {payload['guild_id']} {entry['field']}"
                    )

        role_matches = payload.get("role_matches") or []
        if role_matches:
            lines.extend(["", "Roles"])
            for entry in role_matches:
                lines.append(f"{entry['label']}:")
                lines.append(f"  current: {GuildRoutes._format_scan_current(entry)}")
                lines.append(f"  suggested: {GuildRoutes._format_scan_suggested(entry)}")
                suggested = entry.get("suggested")
                current = entry.get("current") or {}
                if suggested is not None and (
                    current.get("id") != suggested.get("id") or not current.get("found", True)
                ):
                    lines.append(
                        "  auto-apply: "
                        f"rob auto-apply --guild {payload['guild_id']} {entry['field']}"
                    )
        return "\n".join(lines)

    @staticmethod
    def _format_scan_current(entry: dict[str, Any]) -> str:
        current = entry.get("current") or {}
        current_id = current.get("id")
        if current_id is None:
            return "(not set)"
        if not current.get("found", False):
            return f"{current_id} (not found in live guild scan)"
        if entry.get("type") == "channel":
            return f"#{current.get('name')} ({current_id})"
        return f"@{current.get('name')} ({current_id})"

    @staticmethod
    def _format_scan_suggested(entry: dict[str, Any]) -> str:
        suggested = entry.get("suggested")
        if suggested is None:
            return "(no obvious match found)"
        if entry.get("type") == "channel":
            return f"#{suggested.get('name')} ({suggested.get('id')})"
        return f"@{suggested.get('name')} ({suggested.get('id')})"

    @staticmethod
    def _format_guild_apply_text(payload: dict[str, Any]) -> str:
        lines = [
            "Guild Auto-Apply",
            f"Guild ID: {payload['guild_id']}",
            f"Guild Name: {payload.get('guild_name') or '(unknown)'}",
            "Selected: "
            + (
                ", ".join(payload.get("selected_fields", []))
                if payload.get("selected_fields")
                else "all"
            ),
        ]
        applied = payload.get("applied") or []
        skipped = payload.get("skipped") or []

        lines.append("")
        lines.append("Applied")
        if not applied:
            lines.append("- nothing changed")
        else:
            for entry in applied:
                prefix = "#" if entry.get("target_type") == "channel" else "@"
                lines.append(
                    f"- {entry['label']}: {prefix}{entry['target_name']} ({entry['target_id']})"
                )

        lines.append("")
        lines.append("Skipped")
        if not skipped:
            lines.append("- nothing skipped")
        else:
            for entry in skipped:
                if entry["reason"] == "already_matches":
                    lines.append(
                        f"- {entry['label']}: already matches {entry['target_name']} ({entry['target_id']})"
                    )
                elif entry["reason"] == "no_suggestion":
                    lines.append(f"- {entry['label']}: no obvious match found")
                else:
                    lines.append(f"- {entry['label']}: {entry['reason']}")

        return "\n".join(lines)

    @staticmethod
    def _format_invalid_scan_options_text(payload: dict[str, Any]) -> str:
        return "\n".join(
            [
                "Guild Auto-Apply",
                "Invalid option list.",
                "Invalid: " + ", ".join(payload.get("invalid_options", [])),
                "Valid: " + ", ".join(payload.get("valid_options", [])),
            ]
        )
