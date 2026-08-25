from __future__ import annotations

import discord

from rob.models import GuildSettings


def member_has_role(member: discord.abc.User | discord.Member | None, role_id: int | None) -> bool:
    if role_id is None or not isinstance(member, discord.Member):
        return False
    return any(role.id == role_id for role in member.roles)


def is_staff_member(member: discord.abc.User | discord.Member | None, settings: GuildSettings | None) -> bool:
    if not isinstance(member, discord.Member):
        return False
    if member.guild_permissions.administrator or member.guild_permissions.manage_guild:
        return True
    return member_has_role(member, settings.mod_role_id if settings is not None else None)
