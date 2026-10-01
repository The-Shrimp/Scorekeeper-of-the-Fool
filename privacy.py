"""Fail closed before sending member details to a Discord channel."""
from config import get_allowed_guild_id
from constants import GAME_NIGHT_ROLE_NAME, COUNCIL_ROLE_NAME


def is_authorized_guild(guild) -> bool:
    return guild is not None and guild.id == get_allowed_guild_id()


def is_member_only_channel(channel) -> bool:
    guild = getattr(channel, "guild", None)
    if not is_authorized_guild(guild):
        return False
    if channel.permissions_for(guild.default_role).view_channel:
        return False
    for role in guild.roles:
        if role == guild.default_role:
            continue
        permissions = channel.permissions_for(role)
        if permissions.view_channel and role.name not in {GAME_NIGHT_ROLE_NAME, COUNCIL_ROLE_NAME} and not role.permissions.administrator:
            return False
    overwrites = getattr(channel, "overwrites", None)
    if overwrites is None:
        return False
    for target, overwrite in overwrites.items():
        if overwrite.view_channel is not True or target in guild.roles:
            continue
        member_roles = getattr(target, "roles", None)
        if member_roles is None or not any(r.name in {GAME_NIGHT_ROLE_NAME, COUNCIL_ROLE_NAME} or r.permissions.administrator for r in member_roles):
            return False
    return True
