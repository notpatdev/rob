"""Assign/remove the configured leaderboard access role for a member.

Test-guild only. Holding the role (``vib_settings.leaderboard_view_role_id``)
grants use of ``/leaderboard`` and, via Discord channel permissions configured
on the role, read access to the ``#leaderboard`` channel. Members opt in/out
through the Dom/me DM setup or the ``/preferences`` command; this helper makes
their actual Discord role membership match that choice.

Rob needs the ``Manage Roles`` permission and must sit above the access role in
the role hierarchy for assignment to succeed. The helper never raises — it logs
and returns ``False`` when it cannot complete the change so callers can surface
a soft warning instead of failing the whole interaction.
"""

from __future__ import annotations

import logging

import discord

from rob.config.guilds import is_new_system_guild

log = logging.getLogger(__name__)


async def apply_leaderboard_access(
    bot: discord.Client,
    *,
    guild_id: int,
    user_id: int,
    enabled: bool,
) -> bool:
    """Make ``user_id``'s leaderboard access role match ``enabled``.

    Returns ``True`` when the member's role state matches ``enabled`` afterwards
    (including no-op cases), ``False`` when Rob could not complete the change
    (no role configured, role/guild/member missing, or missing permissions).
    """

    if not is_new_system_guild(guild_id):
        return False

    settings_repo = getattr(bot, "guild_settings_repo", None) or getattr(
        bot, "vib_settings_repo", None
    )
    if settings_repo is None:
        return False
    try:
        settings = await settings_repo.get(guild_id)
    except Exception:
        log.exception("Leaderboard access: settings lookup failed guild_id=%s", guild_id)
        return False

    role_id = (
        getattr(settings, "leaderboard_view_role_id", None)
        if settings is not None
        else None
    )
    if role_id is None:
        log.info(
            "Leaderboard access role not configured; skipping guild_id=%s user_id=%s",
            guild_id,
            user_id,
        )
        return False

    guild = bot.get_guild(guild_id)
    if guild is None:
        log.warning("Leaderboard access: guild unavailable guild_id=%s", guild_id)
        return False

    role = guild.get_role(role_id)
    if role is None:
        log.warning("Leaderboard access role %s not found in guild %s", role_id, guild_id)
        return False

    member = guild.get_member(user_id)
    if member is None:
        try:
            member = await guild.fetch_member(user_id)
        except (discord.NotFound, discord.HTTPException):
            log.warning(
                "Leaderboard access: member %s not found in guild %s", user_id, guild_id
            )
            return False

    has_role = any(getattr(r, "id", None) == role_id for r in member.roles)
    try:
        if enabled and not has_role:
            await member.add_roles(role, reason="Leaderboard access opt-in")
            log.info(
                "Granted leaderboard access role to user_id=%s guild_id=%s",
                user_id,
                guild_id,
            )
        elif not enabled and has_role:
            await member.remove_roles(role, reason="Leaderboard access opt-out")
            log.info(
                "Removed leaderboard access role from user_id=%s guild_id=%s",
                user_id,
                guild_id,
            )
    except discord.Forbidden:
        log.warning(
            "Leaderboard access: missing permission to modify roles for user_id=%s "
            "guild_id=%s (Rob needs Manage Roles and must rank above the access role).",
            user_id,
            guild_id,
        )
        return False
    except discord.HTTPException:
        log.exception(
            "Leaderboard access: role update failed user_id=%s guild_id=%s",
            user_id,
            guild_id,
        )
        return False
    return True


__all__ = ["apply_leaderboard_access"]
