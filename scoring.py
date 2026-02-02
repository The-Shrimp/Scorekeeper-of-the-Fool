"""
scoring.py
Score recording + scoreboard display.

Why separate from aliases:
- scoring writes/reads split score CSVs
- aliases cares about identity mapping

Best practice:
- Always store DiscordID in rows you create going forward.
- Keep CSV schema consistent to support migration and analytics later.
"""

import os
import pandas as pd
import discord
from datetime import datetime
from typing import Optional

from constants import COUNCIL_ROLE_NAME
from utils_split import determine_split
from aliases import resolve_aliases_to_members, get_alias_for_member

def _has_council_role(member: discord.Member) -> bool:
    return any(r.name == COUNCIL_ROLE_NAME for r in getattr(member, "roles", []))

def register(bot):
    @bot.tree.command(name="updatescore", description="Update the score of a player")
    async def update_score(
        interaction: discord.Interaction,
        player_name: str,
        amount: float,
        game_name: str,
        date: str = None,
        notes: str = "",
    ):
        if date is None:
            date = datetime.now().strftime("%m/%d/%Y")
        else:
            try:
                datetime.strptime(date, "%m/%d/%Y")
            except ValueError:
                await interaction.response.send_message("Invalid date format. Please use mm/dd/yyyy.", ephemeral=True)
                return

        split = determine_split(date)
        year = date.split("/")[-1]
        file_name = f"{year}_{split}.csv"

        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        if os.path.exists(file_name):
            df = pd.read_csv(file_name)
        else:
            df = pd.DataFrame(columns=["PlayerID", "Score", "GameName", "Date", "Notes"])

        new_row = pd.DataFrame({
            "PlayerID": [player_name],
            "Score": [amount],
            "GameName": [game_name],
            "Date": [date],
            "Notes": [notes],
        })

        df = pd.concat([df, new_row], ignore_index=True)
        df.to_csv(file_name, index=False)

        await interaction.response.send_message(
            f"Score updated for {player_name} in {game_name} with {amount} points on {date}. Notes: {notes}"
        )

    @bot.tree.command(name="scoreboardleaders", description="Display the leaders and runners-up of the scoreboard")
    async def display_scoreboard_leaders(interaction: discord.Interaction):
        split = determine_split(datetime.now().strftime("%m/%d/%Y"))
        year = datetime.now().year
        file_name = f"{year}_{split}.csv"

        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        if not os.path.exists(file_name):
            await interaction.response.send_message("No data available for this split.")
            return

        df = pd.read_csv(file_name)
        score_summary = df.groupby("PlayerID")["Score"].sum().reset_index()
        score_summary_sorted = score_summary.sort_values(by="Score", ascending=False)

        max_score = score_summary_sorted.iloc[0]["Score"]
        leaders = score_summary_sorted[score_summary_sorted["Score"] == max_score]

        if len(score_summary_sorted) > len(leaders):
            runner_up_score = score_summary_sorted.iloc[len(leaders)]["Score"]
            runners_up = score_summary_sorted[score_summary_sorted["Score"] == runner_up_score]
            runner_up_list = "\n".join([f"- {p} ({s} points)" for p, s in zip(runners_up["PlayerID"], runners_up["Score"])])
            runner_up_text = f"Runner-up:\n{runner_up_list}"
        else:
            runner_up_text = "No runner-up available."

        leaders_list = "\n".join([f"- {p} ({s} points)" for p, s in zip(leaders["PlayerID"], leaders["Score"])])
        response = f"Leading Fools:\n{leaders_list}\n\n{runner_up_text}"
        await interaction.response.send_message(response)

    @bot.tree.command(name="scoreboard", description="Display the scoreboard for a specified year and split")
    async def display_scoreboard(interaction: discord.Interaction, year: int, split: int):
        if split not in [1, 2]:
            await interaction.response.send_message("Invalid split. Please enter 1 or 2.", ephemeral=True)
            return

        file_name = f"{year}_Split{split}.csv"

        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        if not os.path.exists(file_name):
            await interaction.response.send_message("No data available for this split.", ephemeral=True)
            return

        df = pd.read_csv(file_name)
        score_summary = df.groupby("PlayerID")["Score"].sum().reset_index()
        score_summary_sorted = score_summary.sort_values(by="PlayerID")

        response = f"## {year} Split - {split} ##\n\n"
        response += "**The Contestants Are:** \n\n"
        response += "\n\n".join([f"**{row['PlayerID']} - {row['Score']} points**" for _, row in score_summary_sorted.iterrows()])
        await interaction.response.send_message(response)

    @bot.tree.command(name="stats", description="Display statistics for a specified player, year, and split")
    async def display_player_stats(interaction: discord.Interaction, playername: str, year: int = None, split: int = None):
        if year is None or split is None:
            current_date = datetime.now()
            year = year if year is not None else current_date.year
            split = split if split is not None else (1 if current_date.month <= 6 else 2)

        file_name = f"{year}_Split{split}.csv"

        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        if not os.path.exists(file_name):
            await interaction.response.send_message(f"No data available for {year} Split {split}.", ephemeral=True)
            return

        df = pd.read_csv(file_name)
        player_data = df[df["PlayerID"] == playername]

        if player_data.empty:
            await interaction.response.send_message(f"@{playername} has no data for {year} Split {split}.", ephemeral=True)
            return

        total_points = player_data["Score"].sum()
        best_games = player_data.groupby("GameName")["Score"].sum().reset_index()
        max_score = best_games["Score"].max()
        best_games = best_games[best_games["Score"] == max_score]["GameName"].tolist()

        total_unique_dates = len(df["Date"].unique())
        player_unique_dates = len(player_data["Date"].unique())
        attendance = f"{player_unique_dates} out of {total_unique_dates}"

        response = f"@{playername} here are your stats:\n"
        response += f"Points: {total_points}\n"
        response += f"Best Game: {', '.join(best_games)}\n"
        response += f"Game Nights attended: {attendance}"
        await interaction.response.send_message(response)

    @bot.tree.command(
        name="gamescore",
        description="Record a game result; uses aliases or @mentions for the current split (Game Night Council only).",
    )
    async def gamescore(
        interaction: discord.Interaction,
        game_name: str,
        points: float,
        winners: str,
        other_players: str,
        date: Optional[str] = None,
        notes: Optional[str] = None,
    ):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        if not _has_council_role(interaction.user):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        guild = interaction.guild

        if not date or not date.strip():
            date_obj = datetime.now()
        else:
            try:
                date_obj = datetime.strptime(date.strip(), "%m/%d/%Y")
            except ValueError:
                await interaction.response.send_message(
                    "Please provide the date in `MM/DD/YYYY` format, e.g. `12/07/2024`.",
                    ephemeral=True,
                )
                return

        date_str = date_obj.strftime("%m/%d/%Y")
        year = date_obj.year
        split = determine_split(date_str)
        file_name = f"{year}_{split}.csv"

        winner_members, unresolved_winners = resolve_aliases_to_members(guild, winners)
        if not winner_members:
            await interaction.response.send_message(
                "Winner(s) field must include at least one valid alias or @mention. I could not match any of them.",
                ephemeral=True,
            )
            return

        other_members, unresolved_others = resolve_aliases_to_members(guild, other_players)
        if not other_members:
            await interaction.response.send_message(
                "Other Players field must include at least one valid alias or @mention. I could not match any of them.",
                ephemeral=True,
            )
            return

        winner_ids = {m.id for m in winner_members}
        filtered_others = [m for m in other_members if m.id not in winner_ids]
        other_members = []
        seen = set()
        for m in filtered_others:
            if m.id not in seen:
                other_members.append(m)
                seen.add(m.id)

        if not other_members:
            await interaction.response.send_message(
                "All Other Players you provided are already listed as winners. Please list only non-winning players.",
                ephemeral=True,
            )
            return

        unresolved_all = unresolved_winners + unresolved_others
        if unresolved_all:
            msg = "I couldn't match the following name(s) to any member:\n"
            msg += ", ".join(f"`{name}`" for name in unresolved_all)
            msg += "\n\nPlease set aliases with `/setalias` or use exact names/@mentions."
            await interaction.response.send_message(msg, ephemeral=True)
            return

        notes_text = (notes or "").strip()
        rows = []

        for w in winner_members:
            rows.append({
                "DiscordID": str(w.id),
                "PlayerID": get_alias_for_member(w),
                "Score": float(points),
                "GameName": game_name,
                "Date": date_str,
                "Notes": notes_text,
            })

        for o in other_members:
            rows.append({
                "DiscordID": str(o.id),
                "PlayerID": get_alias_for_member(o),
                "Score": 0.0,
                "GameName": game_name,
                "Date": date_str,
                "Notes": notes_text,
            })

        new_df = pd.DataFrame(rows)

        if os.path.exists(file_name):
            existing_df = pd.read_csv(file_name)

            if "DiscordID" not in existing_df.columns:
                existing_df["DiscordID"] = ""

            for col in existing_df.columns:
                if col not in new_df.columns:
                    new_df[col] = ""
            for col in new_df.columns:
                if col not in existing_df.columns:
                    existing_df[col] = ""

            combined_df = pd.concat([existing_df, new_df], ignore_index=True)
            combined_df.to_csv(file_name, index=False)
        else:
            new_df.to_csv(file_name, index=False)

        winners_list = ", ".join(get_alias_for_member(w) for w in winner_members)
        others_list = ", ".join(get_alias_for_member(o) for o in other_members)
        notes_display = notes_text if notes_text else "None"
        all_players_list = ", ".join([get_alias_for_member(m) for m in (winner_members + other_members)])

        summary = (
            f"Score recorded for **{game_name}** on {date_str}.\n"
            f"Winner(s) ({points} pts): {winners_list}\n"
            f"Other Players (0 pts): {others_list}\n"
            f"All Players: {all_players_list}\n"
            f"Notes: {notes_display}"
        )

        await interaction.response.send_message(summary)
