import discord
from discord.ext import commands
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Create the bot instance
bot = commands.Bot(command_prefix="/", intents=discord.Intents.all())