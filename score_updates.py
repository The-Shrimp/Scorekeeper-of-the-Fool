import discord
from discord.ext import commands
import os
from datetime import datetime
from dotenv import load_dotenv

@bot.tree.command(name="updatescore", description="Update the score of a player")
async def update_score(interaction: discord.Interaction,
                    player_name: str,
                    amount: float,
                    game_name: str,
                    date: str = None,  # Now date is an optional parameter
                    notes: str = ""):  # notes parameter is optional and defaults to an empty string
    # If no date is provided, use the current date
    if date is None:
        date = datetime.now().strftime('%m/%d/%Y')
    else:
        try:
            # This checks if the provided date is in the correct format
            datetime.strptime(date, '%m/%d/%Y')
        except ValueError:
            await interaction.response.send_message("Invalid date format. Please use mm/dd/yyyy.", ephemeral=True)
            return

    split = determine_split(date)
    year = date.split('/')[-1]
    file_name = f"{year}_{split}.csv"

    if not interaction.guild:  # Check if the interaction is within a guild
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    # Load existing data or initialize a new DataFrame if the file does not exist
    if os.path.exists(file_name):
        df = pd.read_csv(file_name)
    else:
        # Define the DataFrame with the columns including Date and Notes
        df = pd.DataFrame(columns=["PlayerID", "Score", "GameName", "Date", "Notes"])
        df = df.astype({"PlayerID": "str", "Score": "float", "GameName": "str", "Date": "str", "Notes": "str"})

    # Create a new row for each score update
    new_row = pd.DataFrame({"PlayerID": [player_name], "Score": [amount], "GameName": [game_name], "Date": [date], "Notes": [notes]})
    new_row = new_row.astype({"PlayerID": "str", "Score": "float", "GameName": "str", "Date": "str", "Notes": "str"})  # Ensure data types are aligned

    # Concatenate the new row to the existing DataFrame
    df = pd.concat([df, new_row], ignore_index=True)

    # Save the updated DataFrame to the CSV
    df.to_csv(file_name, index=False)
    await interaction.response.send_message(f"Score updated for {player_name} in {game_name} with {amount} points on {date}. Notes: {notes}")

    # Command to display the leading and runner-up players
@bot.tree.command(name="scoreboardleaders", description="Display the leaders and runners-up of the scoreboard")
async def display_scoreboard_leaders(interaction: discord.Interaction):
    split = determine_split(datetime.now().strftime('%m/%d/%Y'))
    year = datetime.now().year
    file_name = f"{year}_{split}.csv"

    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    if os.path.exists(file_name):
        df = pd.read_csv(file_name)
        # Aggregate scores by PlayerID
        score_summary = df.groupby('PlayerID')['Score'].sum().reset_index()
        # Sort summary by Score in descending order
        score_summary_sorted = score_summary.sort_values(by='Score', ascending=False)

        # Get leaders and their score
        max_score = score_summary_sorted.iloc[0]['Score']
        leaders = score_summary_sorted[score_summary_sorted['Score'] == max_score]

        # Check for runner-up existence
        if len(score_summary_sorted) > len(leaders):
            # Find the maximum score that is not the leader's score
            runner_up_score = score_summary_sorted.iloc[len(leaders)]['Score']
            runners_up = score_summary_sorted[score_summary_sorted['Score'] == runner_up_score]
            runner_up_list = '\n'.join([f"- {player} ({points} points)" for player, points in zip(runners_up['PlayerID'], runners_up['Score'])])
            runner_up_text = f"Runner-up:\n{runner_up_list}"
        else:
            runner_up_text = "No runner-up available."

        # Format the leaders list
        leaders_list = '\n'.join([f"- {player} ({points} points)" for player, points in zip(leaders['PlayerID'], leaders['Score'])])
        response = f"Leading Fools:\n{leaders_list}\n\n{runner_up_text}"

        await interaction.response.send_message(response)
    else:
        await interaction.response.send_message("No data available for this split.")

# Display the scoreboard for a specified year and split
@bot.tree.command(name="scoreboard", description="Display the scoreboard for a specified year and split")
async def display_scoreboard(interaction: discord.Interaction, year: int, split: int):
    # Validate the split input
    if split not in [1, 2]:
        await interaction.response.send_message("Invalid split. Please enter 1 or 2.", ephemeral=True)
        return

    file_name = f"{year}_Split{split}.csv"

    if not interaction.guild:
        await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
        return

    try:
        if os.path.exists(file_name):
            df = pd.read_csv(file_name)
            # Aggregate scores by PlayerID
            score_summary = df.groupby('PlayerID')['Score'].sum().reset_index()
            # Sort by PlayerID alphabetically
            score_summary_sorted = score_summary.sort_values(by='PlayerID')

            # Building the response string
            response = f"## {year} Split - {split} ##\n\n"
            response += "**The Contestants Are:** \n\n"
            response += "\n\n".join([f"**{row['PlayerID']} - {row['Score']} points**" for index, row in score_summary_sorted.iterrows()])

            await interaction.response.send_message(response)
        else:
            await interaction.response.send_message("No data available for this split.", ephemeral=True)
    except Exception as e:
        # Generic error handling, consider logging or specific cases
        await interaction.response.send_message(f"An error occurred: {str(e)}", ephemeral=True)