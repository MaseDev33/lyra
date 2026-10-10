from __future__ import annotations

import discord


async def is_server_manager(member: discord.Member | discord.User | None) -> bool:
    if member is None:
        return False
    if not isinstance(member, discord.Member):
        return False
    return member.guild_permissions.manage_guild or member.guild_permissions.administrator
