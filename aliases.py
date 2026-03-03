"""
aliases.py
DB-backed alias system.

- /setalias writes to SQLite (player_aliases table)
- Helper get_alias_for_member() uses DB alias if present
- Resolver supports: @mentions OR alias text OR display_name/username
"""

import re
import discord
from datetime import datetime

from constants import COUNCIL_ROLE_NAME
import db

def _has_council_role(member: discord.Member) -> bool:
    return any(r.name == COUNCIL_ROLE_NAME for r in getattr(member, "roles", []))

def get_alias_for_member(member: discord.Member) -> str:
    """Return alias if present, else display_name."""
    a = db.get_alias(member.id)
    return a if a else member.display_name

def resolve_aliases_to_members(guild: discord.Guild, raw_names: str):
    """
    Accepts aliases, display names, usernames, or @mentions in a single string.
    Returns: (members, unresolved_tokens)
    """
    raw_names = (raw_names or "").strip()
    if not raw_names:
        return [], []

    alias_map = db.load_alias_map()  # discord_id -> alias
    # reverse map alias(lower) -> discord_id
    alias_to_id = {a.lower(): did for did, a in alias_map.items() if a}

    members = []
    unresolved = []
    seen_ids = set()

    # Mentions first
    mention_ids = re.findall(r"<@!?(\d+)>", raw_names)
    for id_str in mention_ids:
        mid = int(id_str)
        m = guild.get_member(mid)
        if not m:
            unresolved.append(f"<@{id_str}>")
        else:
            if mid not in seen_ids and not m.bot:
                members.append(m)
                seen_ids.add(mid)

    # Remaining tokens (comma-separated)
    no_mentions = re.sub(r"<@!?\d+>", "", raw_names)
    tokens = [t.strip() for t in no_mentions.split(",") if t.strip()]

    for token in tokens:
        key = token.lower()
        m = None

        # Alias lookup
        if key in alias_to_id:
            m = guild.get_member(int(alias_to_id[key]))

        # Fallback: display_name / username
        if m is None:
            for cand in guild.members:
                if cand.bot or cand.id in seen_ids:
                    continue
                if cand.display_name.lower() == key or cand.name.lower() == key:
                    m = cand
                    break

        if m is None:
            unresolved.append(token)
        else:
            if m.id not in seen_ids:
                members.append(m)
                seen_ids.add(m.id)

    return members, unresolved


def register(bot):
    db.init_db()

    @bot.tree.command(name="setalias", description="Set or update your game night alias (stored in SQLite).")
    async def set_alias(interaction: discord.Interaction, alias: str):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        clean = (alias or "").strip()
        if not clean:
            await interaction.response.send_message("Please provide a non-empty alias.", ephemeral=True)
            return

        db.upsert_alias(interaction.user.id, clean)
        db.write_audit(action="SET_ALIAS", actor_id=str(interaction.user.id),
                       target_id=str(interaction.user.id), payload={"alias": clean})
        await interaction.response.send_message(f"Alias set to **{clean}**.", ephemeral=True)
