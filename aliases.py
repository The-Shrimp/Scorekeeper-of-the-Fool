"""
aliases.py
Alias storage and identity resolution.

Purpose:
- Store DiscordID -> Alias
- Provide helper get_alias_for_member()
- Resolve input strings containing aliases, display names, or @mentions into Members
- Provide commands: /setalias and /applyalias

Best practices:
- Store DiscordID in score rows to make identity stable even if names change.
"""

import os
import re
import pandas as pd
import discord
from datetime import datetime
from typing import Optional

from constants import ALIASES_FILE, COUNCIL_ROLE_NAME
from utils_split import determine_split

def load_aliases() -> pd.DataFrame:
    """Load aliases.csv or return an empty table with the correct columns."""
    if os.path.exists(ALIASES_FILE):
        try:
            return pd.read_csv(ALIASES_FILE, dtype={"DiscordID": str, "Alias": str})
        except Exception:
            return pd.DataFrame(columns=["DiscordID", "Alias"])
    return pd.DataFrame(columns=["DiscordID", "Alias"])

def save_alias(discord_id: int, alias: str) -> None:
    """Insert or update a single alias in aliases.csv."""
    df = load_aliases()
    discord_id_str = str(discord_id)

    if (df["DiscordID"] == discord_id_str).any():
        df.loc[df["DiscordID"] == discord_id_str, "Alias"] = alias
    else:
        df = pd.concat([df, pd.DataFrame([{"DiscordID": discord_id_str, "Alias": alias}])], ignore_index=True)

    df.to_csv(ALIASES_FILE, index=False)

def get_alias_for_member(member: discord.Member) -> str:
    """Return alias if present, else display_name."""
    df = load_aliases()
    row = df.loc[df["DiscordID"] == str(member.id)]
    if not row.empty:
        return str(row.iloc[0]["Alias"])
    return member.display_name

def resolve_aliases_to_members(guild: discord.Guild, raw_names: str):
    """
    Accepts aliases, display names, usernames, or @mentions in a single string.
    Example: "Belle, <@123>, Phil" or "<@123><@456>".
    Returns: (members, unresolved_tokens)
    """
    raw_names = (raw_names or "").strip()
    if not raw_names:
        return [], []

    alias_df = load_aliases()
    alias_map = {}
    for _, row in alias_df.iterrows():
        a = str(row["Alias"]).strip().lower()
        did = str(row["DiscordID"]).strip()
        if a:
            alias_map[a] = did

    members = []
    unresolved = []
    seen_ids = set()

    mention_ids = re.findall(r"<@!?(\d+)>", raw_names)
    for id_str in mention_ids:
        mid = int(id_str)
        m = guild.get_member(mid)
        if not m:
            unresolved.append(f"<@{id_str}>")
        else:
            if mid not in seen_ids:
                members.append(m)
                seen_ids.add(mid)

    no_mentions = re.sub(r"<@!?\d+>", "", raw_names)
    tokens = [t.strip() for t in no_mentions.split(",") if t.strip()]

    for token in tokens:
        key = token.lower()
        m = None

        if key in alias_map:
            m = guild.get_member(int(alias_map[key]))

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

def _has_council_role(member: discord.Member) -> bool:
    return any(r.name == COUNCIL_ROLE_NAME for r in getattr(member, "roles", []))

def register(bot):
    @bot.tree.command(name="setalias", description="Set or update your game night alias")
    async def set_alias(interaction: discord.Interaction, alias: str):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        clean_alias = (alias or "").strip()
        if not clean_alias:
            await interaction.response.send_message("Please provide a non-empty alias.", ephemeral=True)
            return

        save_alias(interaction.user.id, clean_alias)
        await interaction.response.send_message(f"Alias set to **{clean_alias}** for {interaction.user.mention}.", ephemeral=True)

    @bot.tree.command(name="applyalias", description="Apply current aliases to all PlayerIDs in the current split's scores.")
    async def apply_alias_command(interaction: discord.Interaction):
        """
        Updates the current split's score CSV so PlayerID matches each user's current alias.
        Uses DiscordID when available; otherwise best-effort mapping by name.
        """
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        if not _has_council_role(interaction.user):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        today = datetime.now()
        date_str = today.strftime("%m/%d/%Y")
        year = today.year
        split = determine_split(date_str)
        file_name = f"{year}_{split}.csv"

        if not os.path.exists(file_name):
            await interaction.response.send_message(f"No score file found for the current split (`{file_name}`).", ephemeral=True)
            return

        df = pd.read_csv(file_name, dtype=str)
        if "PlayerID" not in df.columns:
            await interaction.response.send_message("The score file does not have a PlayerID column; cannot apply aliases.", ephemeral=True)
            return

        aliases_df = load_aliases()
        alias_by_id = {str(r["DiscordID"]): str(r["Alias"]) for _, r in aliases_df.iterrows()}

        name_to_alias = {}
        for m in interaction.guild.members:
            if m.bot:
                continue
            a = get_alias_for_member(m)
            for key in {m.display_name, m.name, a}:
                if key not in name_to_alias:
                    name_to_alias[key] = a

        if "DiscordID" in df.columns:
            df["DiscordID"] = df["DiscordID"].astype(str)
            for idx, row in df.iterrows():
                did = row.get("DiscordID")
                if did in alias_by_id:
                    df.at[idx, "PlayerID"] = alias_by_id[did]
                else:
                    old = row["PlayerID"]
                    if old in name_to_alias:
                        df.at[idx, "PlayerID"] = name_to_alias[old]
        else:
            for idx, row in df.iterrows():
                old = row["PlayerID"]
                if old in name_to_alias:
                    df.at[idx, "PlayerID"] = name_to_alias[old]

        df.to_csv(file_name, index=False)
        await interaction.response.send_message(f"Applied aliases to PlayerID in `{file_name}`.", ephemeral=True)
