"""
bot.py
Entry point.

Responsibilities:
- Create bot instance + intents
- Register commands/events from modules
- Handle on_ready: sync commands + reconcile active invitation
- Run bot using token from config

Best practice:
- Keep bot wiring here; keep business logic in modules.
"""

import discord
from discord.ext import commands

from config import load_config
from constants import INTRODUCTION_FILE
import aliases
import scoring
import schedule
import rsvp

def create_bot() -> commands.Bot:
    intents = discord.Intents.all()
    bot = commands.Bot(command_prefix="/", intents=intents)

    # Register commands
    aliases.register(bot)
    scoring.register(bot)
    schedule.register(bot)

    # Register event listeners
    rsvp.register(bot)

    # Introduction command lives nicely here as it's tiny and “bot-facing”
    try:
        with open(INTRODUCTION_FILE, "r", encoding="utf-8") as f:
            introduction = f.read()
    except FileNotFoundError:
        introduction = "Welcome to Game Night!"

    @bot.tree.command(name="introductions")
    async def introductions(interaction: discord.Interaction):
        await interaction.response.send_message(
            f"Hello, {interaction.user.mention}! \n {introduction}",
            ephemeral=True
        )

    @bot.event
    async def on_ready():
        print("Bot is ready")
        try:
            synced = await bot.tree.sync()
            print(f"Synced {len(synced)} command(s)")
        except Exception as e:
            print(f"Error in syncing commands: {e}")

        # Startup reconciliation for “offline reaction changes”
        try:
            for g in bot.guilds:
                await rsvp.reconcile_active_invitation(g)
        except Exception as e:
            print(f"[startup] Error while reconciling active invitation: {e}")

    return bot

def main():
    cfg = load_config()
    bot = create_bot()
    bot.run(cfg["DISCORD_BOT_TOKEN"])

if __name__ == "__main__":
    main()
