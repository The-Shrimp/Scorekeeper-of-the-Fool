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
from constants import INTRODUCTION_FILE, COUNCIL_ROLE_NAME
import aliases
import scoring
import schedule
import rsvp
import competitive_scoring


def create_bot() -> commands.Bot:
    intents = discord.Intents.all()
    bot = commands.Bot(command_prefix="/", intents=intents)

    def _has_council_role(user: discord.abc.User) -> bool:
        return isinstance(user, discord.Member) and any(
            role.name == COUNCIL_ROLE_NAME for role in getattr(user, "roles", [])
        )

    # Register commands
    aliases.register(bot)
    scoring.register(bot)
    schedule.register(bot)
    competitive_scoring.register(bot)


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

    @bot.tree.command(name="scoring_help")
    async def scoring_help(interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        if not _has_council_role(interaction.user):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        text = (
            "**Council Scoring Help**\n"
            "`/loggame game:<name> minutes:<int> players:<roster> winners:<roster> [date] [notes]`\n"
            "- Use @mentions or comma-separated aliases/display names for rosters.\n"
            "- Example: `/loggame game:\"Clue\" minutes:45 players:\"@A, Shrimp, Snake\" winners:\"Shrimp\"`\n\n"
            "`/editgame <game_id> [game] [minutes] [players] [winners] [date] [notes]`\n"
            "- Creates a corrected replacement record and keeps the original in history.\n\n"
            "`/leaderboard`, `/mystats`, `/stats`, `/splitstats`\n"
            "- Use these to verify the active scoring state after updates.\n\n"
            "**Recommended flow:**\n"
            "1. Log games with `/loggame`.\n"
            "2. Fix mistakes with `/editgame`.\n"
            "3. Use `/gameinfo <id>` to inspect a record after a correction."
        )
        await interaction.response.send_message(text, ephemeral=True)

    @bot.tree.command(name="audit_help")
    async def audit_help(interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        if not _has_council_role(interaction.user):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        text = (
            "**Council Audit Help**\n"
            "`/gameinfo <game_id>`\n"
            "- Inspect an active row and see whether it supersedes another game or has been superseded.\n\n"
            "`/auditgame <game_id> [limit]`\n"
            "- Show recent audit entries tied to that game id and any edit replacement chain.\n\n"
            "`/undo <game_id>` or `/undo_last`\n"
            "- Remove a game from active scoring and place it in review.\n\n"
            "`/reviewqueue [limit]`\n"
            "- See which games are currently in review.\n\n"
            "`/reviewgame <game_id>`\n"
            "- Inspect a removed game before deciding to restore it.\n\n"
            "`/recover <game_id>`\n"
            "- Restore a reviewed game to active scoring.\n\n"
            "**Recommended flow:**\n"
            "1. Check `/gameinfo` first.\n"
            "2. Use `/auditgame` if you need the recent mutation trail.\n"
            "3. Prefer `/editgame` for corrections.\n"
            "4. Use `/undo` only when the row should leave active scoring.\n"
            "5. Use `/reviewgame` before `/recover`."
        )
        await interaction.response.send_message(text, ephemeral=True)

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
