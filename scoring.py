"""
scoring.py (Legacy CSV era) — Polars version

Keeps your old CSV-based commands working without pandas/numpy.
Renames /stats -> /legacystats to avoid colliding with competitive scoring /stats.
"""

import os
import polars as pl
import discord
from datetime import datetime

from utils_split import determine_split

def register(bot):
    os.makedirs("data/legacy", exist_ok=True)

    @bot.tree.command(name="updatescore", description="(Legacy) Append a score row to the split CSV.")
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
                await interaction.response.send_message("Invalid date format. Use mm/dd/yyyy.", ephemeral=True)
                return

        split = determine_split(date)
        year = date.split("/")[-1]
        file_name = f"data/legacy/{year}_{split}.csv"

        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        if os.path.exists(file_name):
            df = pl.read_csv(file_name, infer_schema_length=0)
        else:
            df = pl.DataFrame({
                "PlayerID": [],
                "Score": [],
                "GameName": [],
                "Date": [],
                "Notes": [],
            })

        new_row = pl.DataFrame([{
            "PlayerID": player_name,
            "Score": float(amount),
            "GameName": game_name,
            "Date": date,
            "Notes": notes,
        }])

        out = pl.concat([df, new_row], how="vertical")
        out.write_csv(file_name)

        await interaction.response.send_message(
            f"(Legacy) Score updated for {player_name} in {game_name} with {amount} points on {date}. Notes: {notes}",
            ephemeral=True,
        )

    @bot.tree.command(name="scoreboardleaders", description="(Legacy) Display leaders and runners-up for current split.")
    async def display_scoreboard_leaders(interaction: discord.Interaction):
        split = determine_split(datetime.now().strftime("%m/%d/%Y"))
        year = datetime.now().year
        file_name = f"data/legacy/{year}_{split}.csv"

        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        if not os.path.exists(file_name):
            await interaction.response.send_message("No legacy data available for this split.", ephemeral=True)
            return

        df = pl.read_csv(file_name, infer_schema_length=0)
        if "PlayerID" not in df.columns or "Score" not in df.columns:
            await interaction.response.send_message("Legacy CSV missing PlayerID/Score columns.")
            return

        score_summary = df.group_by("PlayerID").agg(pl.col("Score").cast(pl.Float64).sum().alias("Score"))
        score_sorted = score_summary.sort("Score", descending=True)

        max_score = score_sorted[0, "Score"]
        leaders = score_sorted.filter(pl.col("Score") == max_score)

        if score_sorted.height > leaders.height:
            runner_up_score = score_sorted[leaders.height, "Score"]
            runners_up = score_sorted.filter(pl.col("Score") == runner_up_score)
            runner_up_list = "\n".join([f"- {p} ({s} points)" for p, s in zip(runners_up["PlayerID"], runners_up["Score"])])
            runner_up_text = f"Runner-up:\n{runner_up_list}"
        else:
            runner_up_text = "No runner-up available."

        leaders_list = "\n".join([f"- {p} ({s} points)" for p, s in zip(leaders["PlayerID"], leaders["Score"])])
        response = f"(Legacy) Leading Fools:\n{leaders_list}\n\n{runner_up_text}"
        await interaction.response.send_message(response, ephemeral=True)

    @bot.tree.command(name="scoreboard", description="(Legacy) Display scoreboard for a specified year and split")
    async def display_scoreboard(interaction: discord.Interaction, year: int, split: int):
        if split not in [1, 2]:
            await interaction.response.send_message("Invalid split. Enter 1 or 2.", ephemeral=True)
            return

        file_name = f"data/legacy/{year}_Split{split}.csv"

        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        if not os.path.exists(file_name):
            await interaction.response.send_message("No legacy data available for this split.", ephemeral=True)
            return

        df = pl.read_csv(file_name, infer_schema_length=0)
        if "PlayerID" not in df.columns or "Score" not in df.columns:
            await interaction.response.send_message("Legacy CSV missing PlayerID/Score columns.", ephemeral=True)
            return

        score_summary = df.group_by("PlayerID").agg(pl.col("Score").cast(pl.Float64).sum().alias("Score"))
        score_summary_sorted = score_summary.sort("PlayerID")

        response = f"## (Legacy) {year} Split {split} ##\n\n"
        response += "**Contestants:**\n\n"
        for pid, s in zip(score_summary_sorted["PlayerID"], score_summary_sorted["Score"]):
            response += f"**{pid} - {s} points**\n"
        await interaction.response.send_message(response, ephemeral=True)

    @bot.tree.command(name="legacystats", description="(Legacy) Display statistics for a specified player, year, and split")
    async def display_player_stats(interaction: discord.Interaction, playername: str, year: int = None, split: int = None):
        if year is None or split is None:
            current_date = datetime.now()
            year = year if year is not None else current_date.year
            split = split if split is not None else (1 if current_date.month <= 6 else 2)

        file_name = f"data/legacy/{year}_Split{split}.csv"

        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        if not os.path.exists(file_name):
            await interaction.response.send_message(f"No legacy data available for {year} Split {split}.", ephemeral=True)
            return

        df = pl.read_csv(file_name, infer_schema_length=0)
        if "PlayerID" not in df.columns:
            await interaction.response.send_message("Legacy CSV missing PlayerID column.", ephemeral=True)
            return

        player_data = df.filter(pl.col("PlayerID") == playername)

        if player_data.height == 0:
            await interaction.response.send_message(f"{playername} has no legacy data for {year} Split {split}.", ephemeral=True)
            return

        total_points = float(player_data["Score"].cast(pl.Float64).sum())
        best_games = player_data.group_by("GameName").agg(pl.col("Score").cast(pl.Float64).sum().alias("Score"))
        max_score = float(best_games["Score"].max())
        best_games_list = best_games.filter(pl.col("Score") == max_score)["GameName"].to_list()

        total_unique_dates = df["Date"].n_unique() if "Date" in df.columns else 0
        player_unique_dates = player_data["Date"].n_unique() if "Date" in player_data.columns else 0
        attendance = f"{player_unique_dates} out of {total_unique_dates}"

        response = (
            f"(Legacy) {playername} stats:\n"
            f"Points: {total_points}\n"
            f"Best Game: {', '.join(best_games_list)}\n"
            f"Legacy nights attended (approx): {attendance}"
        )
        await interaction.response.send_message(response, ephemeral=True)
