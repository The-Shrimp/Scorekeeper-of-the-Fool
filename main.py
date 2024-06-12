import os
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Initialize the bot
intents = Intents.DEFAULT
bot = bot(command_prefix='/', intents=intents)

# Helper function to determine the split based on the date
def determine_split(date):
    month, day, year = map(int, date.split('/'))
    return "Split1" if 1 <= month <= 6 else "Split2"

# Slash command to update the score of a player
@bot.slash_command(
    name="updatescore",
    description="Update the score of a player",
    options=[
        Option(name="player_name", description="Name of the player", type=OptionType.STRING, required=True),
        Option(name="amount", description="Amount to add or subtract from the score", type=OptionType.FLOAT, required=True),
        Option(name="date", description="Date in DD/MM/YYYY format", type=OptionType.STRING, required=True),
        Option(name="game_name", description="Name of the game played", type=OptionType.STRING, required=True)
    ]
)
async def update_score(ctx: CommandContext, player_name: str, amount: int, date: str, game_name: str):
    split = determine_split(date)
    year = date.split('/')[-1]
    file_name = f"{year}_{split}.csv"

    if os.path.exists(file_name):
        df = pd.read_csv(file_name)
    else:
        df = pd.DataFrame(columns=["PlayerID", "Score", "GameName"])

    if player_name in df["PlayerID"].values:
        df.loc[df["PlayerID"] == player_name, "Score"] += amount
    else:
        df = df.append({"PlayerID": player_name, "Score": amount, "GameName": game_name}, ignore_index=True)

    df.to_csv(file_name, index=False)
    await ctx.send(f"Score updated for {player_name} in {file_name}")

# Slash command to display the scoreboard
@bot.slash_command(
    name="scoreboard",
    description="Display the scoreboard"
)
async def display_scoreboard(ctx: CommandContext):
    split = determine_split(datetime.now().strftime('%m/%d/%Y'))
    year = datetime.now().year
    file_name = f"{year}_{split}.csv"

    if os.path.exists(file_name):
        df = pd.read_csv(file_name)
        df_sorted = df.sort_values(by="Score", ascending=False).reset_index(drop=True)
        leaders = df_sorted[df_sorted["Score"] == df_sorted["Score"].max()]["PlayerID"].tolist()
        
        if len(leaders) == 1:
            response = f"Leading Fool: {leaders[0]}"
        else:
            response = "Leading Fools:\n" + '\n'.join([f"- {name}" for name in leaders])

        await ctx.send(response)
    else:
        await ctx.send("No data available for this split.")

bot_token = os.getenv('DISCORD_BOT_TOKEN')
bot.run(bot_token)
