import discord
from discord.ext import commands
import os
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv

# Import commands from command families
from score_updates import update_score, display_scoreboard_leaders, display_scoreboard
from player_stats import display_player_stats

# Load environment variables
load_dotenv()

# Initialize the bot
bot = commands.Bot(command_prefix="/", intents = discord.Intents.all())

# Read introduction from a text file
with open('introduction.txt', 'r', encoding='utf-8') as file:
    introduction = file.read()

@bot.tree.command(name='introductions')
async def hello(interaction: discord.Interaction):
    await interaction.response.send_message(f'Hello, {interaction.user.mention}! \n {introduction}')
    ephemeral=True
@bot.event
async def on_ready():
    print("Bot is ready")
    try:
        synced = await bot.tree.sync()
        print(f'Synced {len(synced)} command(s)')
    except Exception as e:
        print(f'Error in syncing commands: {e}')

# Helper function to determine the split based on the date
def determine_split(date):
    month, day, year = map(int, date.split('/'))
    return "Split1" if 1 <= month <= 6 else "Split2"

# Command to schedule a new game night
@bot.tree.command(name="schedulenew", description="Schedule a new game night")
async def schedule_game_night(interaction: discord.Interaction, date: str = None, time: str = "5:00 PM"):
    # Check if the user has the "Game Night Council" role
    has_role = any(role.name == "Game Night Council" for role in interaction.user.roles)
    if not has_role:
        await interaction.response.send_message("You do not have the required role to schedule a game night.", ephemeral=True)
        return

    # Set default date to upcoming Saturday if not provided
    if date is None:
        today = datetime.now()
        saturday = today + timedelta((5 - today.weekday()) % 7)  # Get the upcoming Saturday
        date = saturday.strftime('%m/%d/%Y')
    else:
        try:
            # Check if the provided date is in the correct format
            datetime.strptime(date, '%m/%d/%Y')
        except ValueError:
            await interaction.response.send_message("Invalid date format. Please use mm/dd/yyyy.", ephemeral=True)
            return

    # Create a new attendance CSV for the scheduled game night
    split = determine_split(date)
    year = date.split('/')[-1]
    file_name = f"attendance_{year}_{split}.csv"

    if not os.path.exists(file_name):
        df = pd.DataFrame(columns=["PlayerID", "Date", "Notes"])
    else:
        df = pd.read_csv(file_name)

    # Add a placeholder to mark this date as a scheduled game night
    new_row = pd.DataFrame({"PlayerID": ["Scheduled"], "Date": [date], "Notes": ["Game Night Initialized"]})
    df = pd.concat([df, new_row], ignore_index=True)
    df.to_csv(file_name, index=False)

    # Send the announcement to the @Game Night role
    game_night_role = discord.utils.get(interaction.guild.roles, name="Game Night")
    if game_night_role:
        await interaction.response.send_message(f"{game_night_role.mention} I would like to welcome you all to join us for a Game Night on \n{date} at {time}")
    else:
        await interaction.response.send_message("Game Night role not found.", ephemeral=True)

# Modified sign-in command
@bot.tree.command(name="signin", description="Sign in for game night")
async def sign_in(interaction: discord.Interaction, player_name: str = None, date: str = None):
    # If no name is provided, use the user's display name
    player_name = player_name if player_name else interaction.user.display_name
    
    # If no date is provided, use the current date
    if date is None:
        date = datetime.now().strftime('%m/%d/%Y')
        is_async = False
    else:
        try:
            # Check if the provided date is in the correct format
            datetime.strptime(date, '%m/%d/%Y')
            is_async = True
        except ValueError:
            await interaction.response.send_message("Invalid date format. Please use mm/dd/yyyy.", ephemeral=True)
            return

    split = determine_split(date)
    year = date.split('/')[-1]
    file_name = f"attendance_{year}_{split}.csv"

    if not os.path.exists(file_name):
        await interaction.response.send_message("No game night has been scheduled for this date.", ephemeral=True)
        return

    df = pd.read_csv(file_name)

    # Check if the date is valid for sign-in (i.e., it has been initialized)
    if df[(df['Date'] == date) & (df['PlayerID'] == "Scheduled")].empty:
        await interaction.response.send_message("No game night has been scheduled for this date.", ephemeral=True)
        return

    # Check if the player already signed in on the provided date
    if not df[(df['PlayerID'] == player_name) & (df['Date'] == date)].empty:
        await interaction.response.send_message("You have already signed in for this date.", ephemeral=True)
        return

    # Add new attendance record with async tag if retroactive
    notes = "Async" if is_async else ""
    new_row = pd.DataFrame({"PlayerID": [player_name], "Date": [date], "Notes": [notes]})
    df = pd.concat([df, new_row], ignore_index=True)
    df.to_csv(file_name, index=False)

    await interaction.response.send_message(f"{player_name}, you have signed in for game night on {date}.", ephemeral=True)










bot.tree.add_command(update_score)
bot.tree.add_command(display_scoreboard_leaders)
bot.tree.add_command(display_scoreboard)
bot.tree.add_command(display_player_stats)

bot_token = os.getenv('DISCORD_BOT_TOKEN')
bot.run(bot_token)
