import discord
from discord.ext import commands
import os
import pandas as pd
from datetime import datetime
from bot_instance import bot

# Display statistics for a specified player, year, and split
@bot.tree.command(name="stats", description="Display statistics for a specified player, year, and split")
async def display_player_stats(interaction: discord.Interaction, 
                            playername: str, 
                            year: int = None, 
                            split: int = None):
    # Determine the current year and split if not provided
    if year is None or split is None:
        current_date = datetime.now()
        current_year = current_date.year
        current_split = 1 if current_date.month <= 6 else 2
        year = year if year is not None else current_year
        split = split if split is not None else current_split

    file_name = f"{year}_Split{split}.csv"

    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    try:
        if os.path.exists(file_name):
            df = pd.read_csv(file_name)
            # Filter data for the specified player
            player_data = df[df['PlayerID'] == playername]

            if player_data.empty:
                await interaction.response.send_message(f"@{playername} has no data for {year} Split {split}.", ephemeral=True)
                return

            # Calculate total points
            total_points = player_data['Score'].sum()

            # Identify the best game(s)
            best_games = player_data.groupby('GameName')['Score'].sum().reset_index()
            max_score = best_games['Score'].max()
            best_games = best_games[best_games['Score'] == max_score]['GameName'].tolist()

            # Calculate game nights attended
            total_unique_dates = len(df['Date'].unique())
            player_unique_dates = len(player_data['Date'].unique())
            attendance = f"{player_unique_dates} out of {total_unique_dates}"

            # Build the response
            response = f"@{playername} here are your stats:\n"
            response += f"Points: {total_points}\n"
            response += f"Best Game: {', '.join(best_games)}\n"
            response += f"Game Nights attended: {attendance}"

            await interaction.response.send_message(response)
        else:
            await interaction.response.send_message(f"No data available for {year} Split {split}.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"An error occurred: {str(e)}", ephemeral=True)