"""
Scorekeeper of the Fool — Discord Bot
=====================================

PURPOSE
- Records game night results into split-based CSVs.
- Manages player aliases (DiscordID -> Alias).
- Schedules game night announcements and tracks RSVP reactions.

HOW TO RUN (dev)
- Activate venv (or use VS Code interpreter)
- python main.py

DATA FILES
- aliases.csv
  Columns: DiscordID, Alias

- {YEAR}_Split{1|2}.csv
  Columns (preferred): DiscordID, PlayerID, Score, GameName, Date, Notes

- {YEAR}_Split{1|2}_gamenights.csv
  Columns: Date, Status, Time, Location, Notes, Attendees, Possible Attendees, Afterwards Comments

----------------------------------------------------------------------
INDEX
1) Imports & Configuration
   - constants (role names, channel names, emojis)
   - bot intents / bot initialization

2) Utility Helpers
   2.1 Date/Split helpers (determine_split, upcoming Saturday utilities)
   2.2 Parsing helpers (time parsing, mention/alias parsing)
   2.3 CSV helpers (safe read/merge/write patterns)
   2.4 Logging helpers (optional)

3) Alias System
   - load_aliases()
   - save_alias()
   - get_alias_for_member()
   - /setalias
   - /applyalias

4) Score Recording
   - /updatescore (legacy / optional)
   - /gamescore (current)
   - scoreboard/stat commands (scoreboard, leaders, stats)

5) Scheduling & RSVP
   - schedule CSV helpers (ensure_schedule_file_for_date, update_schedule_entry)
   - /schedulegamenight
   - reaction handlers (on_raw_reaction_add/remove)
   - startup reconcile (reconcile_active_invitation)

6) Bot Lifecycle
   - on_ready()
   - bot.run()

NOTES / TODO
- TODO: /aftergamenight (writes Afterwards Comments)
- TODO: /cancelgamenight (mark cancelled + post notice)
"""

import discord
from discord.ext import commands
import os
import pandas as pd
import re
import calendar
from datetime import datetime, date, timedelta
from dotenv import load_dotenv
from typing import Optional

# Misc variables
YES_EMOJI = "✅"
MAYBE_EMOJI = "❔"
ANNOUNCEMENT_CHANNEL_NAME = "game-night-announcement-board"
GAME_NIGHT_ROLE_NAME = "Game Night"

# Load environment variables
load_dotenv()

# Initialize the bot
bot = commands.Bot(command_prefix="/", intents = discord.Intents.all())

def get_schedule_filename(year: int, split: str) -> str:
    """Return the schedule CSV filename for a given year and split."""
    return f"{year}_{split}_gamenights.csv"


def generate_saturdays_for_split(year: int, split: str):
    """Yield all Saturdays in the given year's split."""
    if split == "Split1":
        start_month, end_month = 1, 6
    else:
        start_month, end_month = 7, 12

    start_date = date(year, start_month, 1)
    end_date = date(year, end_month, calendar.monthrange(year, end_month)[1])

    # Move to first Saturday
    cur = start_date
    while cur.weekday() != 5:  # Monday=0 ... Saturday=5
        cur += timedelta(days=1)

    while cur <= end_date:
        yield cur
        cur += timedelta(days=7)


def ensure_schedule_file_for_date(target_date: date) -> str:
    """
    Ensure the schedule CSV for the split covering target_date exists and
    contains entries for all Saturdays in that split.
    Returns the schedule filename.
    """
    date_str = target_date.strftime("%m/%d/%Y")
    year = target_date.year
    split = determine_split(date_str)
    file_name = get_schedule_filename(year, split)

    if os.path.exists(file_name):
        df = pd.read_csv(file_name, dtype=str)
    else:
        # Create an empty DataFrame with the correct columns
        df = pd.DataFrame(
            columns=[
                "Date",
                "Status",
                "Time",
                "Location",
                "Notes",
                "Attendees",
                "Possible Attendees",
                "Afterwards Comments",
            ]
        )

    existing_dates = set(df["Date"].tolist()) if "Date" in df.columns else set()

    # Add any missing Saturdays for this split
    new_rows = []
    for sat in generate_saturdays_for_split(year, split):
        sat_str = sat.strftime("%m/%d/%Y")
        if sat_str not in existing_dates:
            new_rows.append(
                {
                    "Date": sat_str,
                    "Status": "Undecided",
                    "Time": "5 PM",
                    "Location": "Usual Location",
                    "Notes": "None",
                    "Attendees": "",
                    "Possible Attendees": "",
                    "Afterwards Comments": "",
                }
            )

    if new_rows:
        df = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)

    # Ensure Date is unique and sorted
    if not df.empty:
        df = df.drop_duplicates(subset=["Date"])
        # Sort chronologically by parsing MM/DD/YYYY
        df["_sort_date"] = df["Date"].apply(lambda s: datetime.strptime(s, "%m/%d/%Y"))
        df = df.sort_values(by="_sort_date").drop(columns=["_sort_date"])


    df.to_csv(file_name, index=False)
    return file_name

def update_schedule_entry(
    target_date: date,
    status: str = None,
    time_str: str = None,
    location: str = None,
    notes: str = None,
    attendees: str = None,
    possible_attendees: str = None,
    afterwards_comments: str = None,
):
    """Update a single row in the schedule CSV for target_date."""
    schedule_file = ensure_schedule_file_for_date(target_date)
    df = pd.read_csv(schedule_file, dtype=str)

    date_str = target_date.strftime("%m/%d/%Y")
    mask = df["Date"] == date_str

    if not mask.any():
        # If somehow missing, append a default row then recall
        new_row = {
            "Date": date_str,
            "Status": "Undecided",
            "Time": "5 PM",
            "Location": "Usual Location",
            "Notes": "None",
            "Attendees": "",
            "Possible Attendees": "",
            "Afterwards Comments": "",
        }
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
        mask = df["Date"] == date_str

    idx = df.index[mask][0]

    if status is not None:
        df.at[idx, "Status"] = status
    if time_str is not None:
        df.at[idx, "Time"] = time_str
    if location is not None:
        df.at[idx, "Location"] = location
    if notes is not None:
        df.at[idx, "Notes"] = notes
    if attendees is not None:
        df.at[idx, "Attendees"] = attendees
    if possible_attendees is not None:
        df.at[idx, "Possible Attendees"] = possible_attendees
    if afterwards_comments is not None:
        df.at[idx, "Afterwards Comments"] = afterwards_comments

    df.to_csv(schedule_file, index=False)

def parse_list_field(text: str) -> list:
    """Parse a comma-separated list field into a list of stripped strings."""
    if not isinstance(text, str) or not text.strip():
        return []
    return [x.strip() for x in text.split(",") if x.strip()]


def join_list_field(items: list) -> str:
    """Join a list of strings into a comma-separated field."""
    return ", ".join(sorted(set(items)))  # sorted & de-duplicated


def update_schedule_attendance_for_member(
    target_date: date, member: discord.Member, status: str
):
    """
    status: 'yes', 'maybe', or 'none'
    """
    schedule_file = ensure_schedule_file_for_date(target_date)
    df = pd.read_csv(schedule_file, dtype=str)
    date_str = target_date.strftime("%m/%d/%Y")
    mask = df["Date"] == date_str

    if not mask.any():
        return  # nothing to update

    idx = df.index[mask][0]

    attendees = parse_list_field(df.at[idx, "Attendees"])
    maybes = parse_list_field(df.at[idx, "Possible Attendees"])

    label = get_alias_for_member(member)

    # Remove from both lists first
    attendees = [x for x in attendees if x != label]
    maybes = [x for x in maybes if x != label]

    if status == "yes":
        attendees.append(label)
    elif status == "maybe":
        maybes.append(label)
    # 'none' means removed from both

    df.at[idx, "Attendees"] = join_list_field(attendees)
    df.at[idx, "Possible Attendees"] = join_list_field(maybes)

    df.to_csv(schedule_file, index=False)

def get_upcoming_saturday_at_default_time() -> datetime:
    """Return a datetime for the upcoming Saturday at 5 PM."""
    now = datetime.now()
    # Monday=0 ... Saturday=5
    days_ahead = (5 - now.weekday()) % 7
    # If it's already Saturday at or after 5 PM, go to next week
    if days_ahead == 0 and now.hour >= 17:
        days_ahead = 7
    target = now + timedelta(days=days_ahead)
    return target.replace(hour=17, minute=0, second=0, microsecond=0)


def parse_time_input(value: str):
    """
    Parse a time string input by the user (e.g. '5pm', '7:30 pm', '19:00').
    Returns (display_time_str, 24h hour, minute) or (None, None, None) on failure.
    """
    value = value.strip().lower()
    if not value:
        return None, None, None

    # Normalize a space before am/pm
    value = value.replace(" ", "")
    try:
        # 5pm or 5:30pm
        if "am" in value or "pm" in value:
            fmt = "%I%p" if ":" not in value else "%I:%M%p"
            t = datetime.strptime(value, fmt)
        else:
            # 24-hour, e.g. 17:00
            fmt = "%H:%M" if ":" in value else "%H"
            t = datetime.strptime(value, fmt)
        hour = t.hour
        minute = t.minute
        display = t.strftime("%I:%M %p").lstrip("0")  # e.g. "5:00 PM"
        return display, hour, minute
    except ValueError:
        return None, None, None

# --- NEW: alias handling ---

ALIASES_FILE = "aliases.csv"

def load_aliases():
    """Load aliases from aliases.csv, or return an empty DataFrame if none."""
    if os.path.exists(ALIASES_FILE):
        try:
            df = pd.read_csv(ALIASES_FILE, dtype={"DiscordID": str, "Alias": str})
            return df
        except Exception:
            # If there's any issue, start with a clean table
            return pd.DataFrame(columns=["DiscordID", "Alias"])
    else:
        return pd.DataFrame(columns=["DiscordID", "Alias"])

def save_alias(discord_id: int, alias: str):
    """Insert or update a single alias in aliases.csv."""
    df = load_aliases()
    discord_id_str = str(discord_id)

    if (df["DiscordID"] == discord_id_str).any():
        df.loc[df["DiscordID"] == discord_id_str, "Alias"] = alias
    else:
        df = pd.concat(
            [df, pd.DataFrame([{"DiscordID": discord_id_str, "Alias": alias}])],
            ignore_index=True,
        )

    df.to_csv(ALIASES_FILE, index=False)

def get_alias_for_member(member: discord.Member) -> str:
    """
    Return the saved alias for a member if it exists,
    otherwise fall back to their current display name.
    """
    df = load_aliases()
    discord_id_str = str(member.id)
    row = df.loc[df["DiscordID"] == discord_id_str]
    if not row.empty:
        return row.iloc[0]["Alias"]
    return member.display_name

def resolve_aliases_to_members(guild: discord.Guild, raw_names: str):
    """
    Given a comma-separated string of aliases, display names, usernames, or @mentions,
    resolve them to Discord Member objects.

    Returns (members, unresolved_names)
    - members: list[discord.Member]
    - unresolved_names: list[str] of tokens/mentions we couldn't match
    """
    raw_names = (raw_names or "").strip()
    if not raw_names:
        return [], []

    # Load alias table
    alias_df = load_aliases()
    alias_map = {}  # alias_lower -> discord_id_str
    for _, row in alias_df.iterrows():
        alias_str = str(row["Alias"]).strip().lower()
        discord_id_str = str(row["DiscordID"]).strip()
        if alias_str:
            alias_map[alias_str] = discord_id_str

    members = []
    unresolved = []
    seen_ids = set()

    # 1) Handle @mentions anywhere in the string
    mention_ids = re.findall(r"<@!?(\d+)>", raw_names)
    for id_str in mention_ids:
        mid = int(id_str)
        member = guild.get_member(mid)
        if member is None:
            unresolved.append(f"<@{id_str}>")
        else:
            if mid not in seen_ids:
                members.append(member)
                seen_ids.add(mid)

    # Remove mentions from the string before alias/name parsing
    no_mentions = re.sub(r"<@!?\d+>", "", raw_names)

    # 2) Handle aliases / names as comma-separated tokens
    tokens = [t.strip() for t in no_mentions.split(",") if t.strip()]

    for token in tokens:
        key = token.lower()
        member = None

        # a) Try alias table
        if key in alias_map:
            discord_id_str = alias_map[key]
            member = guild.get_member(int(discord_id_str))

        # b) Fallback to matching display_name / username
        if member is None:
            for m in guild.members:
                if m.bot:
                    continue
                if m.id in seen_ids:
                    continue
                if m.display_name.lower() == key or m.name.lower() == key:
                    member = m
                    break

        if member is None:
            unresolved.append(token)
        else:
            if member.id not in seen_ids:
                members.append(member)
                seen_ids.add(member.id)

    return members, unresolved



@bot.tree.command(
    name="applyalias",
    description="Apply current aliases to all PlayerIDs in the current split's scores.",
)
async def apply_alias_command(interaction: discord.Interaction):
    """
    Updates the current split's score CSV so that PlayerID matches each user's current alias.
    Uses DiscordID when available; otherwise falls back to matching by name/display name.
    """

    required_role_name = "Game Night Council"

    # Must be in a server
    if not interaction.guild:
        await interaction.response.send_message(
            "This command can only be used in a server.",
            ephemeral=True,
        )
        return

    # Role check
    if not any(role.name == required_role_name for role in interaction.user.roles):
        await interaction.response.send_message(
            "You do not have permission to use this command.",
            ephemeral=True,
        )
        return

    # Determine "current split" by today's date
    today = datetime.now()
    date_str = today.strftime("%m/%d/%Y")
    year = today.year
    split = determine_split(date_str)  # e.g. "Split1" / "Split2"
    file_name = f"{year}_{split}.csv"

    if not os.path.exists(file_name):
        await interaction.response.send_message(
            f"No score file found for the current split (`{file_name}`).",
            ephemeral=True,
        )
        return

    # Load scores
    df = pd.read_csv(file_name, dtype=str)

    if "PlayerID" not in df.columns:
        await interaction.response.send_message(
            "The score file does not have a PlayerID column; cannot apply aliases.",
            ephemeral=True,
        )
        return

    # Build alias maps
    aliases_df = load_aliases()

    # Map DiscordID -> alias
    alias_by_id = {}
    for _, row in aliases_df.iterrows():
        alias_by_id[str(row["DiscordID"])] = str(row["Alias"])

    # Map possible name/display_name -> alias (best-effort for older rows)
    name_to_alias = {}
    for member in interaction.guild.members:
        if member.bot:
            continue
        alias = get_alias_for_member(member)
        # Use display_name and username as keys
        for key in {member.display_name, member.name, alias}:
            if key not in name_to_alias:
                name_to_alias[key] = alias

    # Update rows
    if "DiscordID" in df.columns:
        # First, use DiscordID when present
        df["DiscordID"] = df["DiscordID"].astype(str)
        for idx, row in df.iterrows():
            discord_id = row.get("DiscordID")
            if discord_id in alias_by_id:
                df.at[idx, "PlayerID"] = alias_by_id[discord_id]
            else:
                # fallback to name-based mapping
                old_name = row["PlayerID"]
                if old_name in name_to_alias:
                    df.at[idx, "PlayerID"] = name_to_alias[old_name]
    else:
        # Older file without DiscordID: best-effort mapping by names
        for idx, row in df.iterrows():
            old_name = row["PlayerID"]
            if old_name in name_to_alias:
                df.at[idx, "PlayerID"] = name_to_alias[old_name]

    # Save back
    df.to_csv(file_name, index=False)

    await interaction.response.send_message(
        f"Applied aliases to PlayerID in `{file_name}`.",
        ephemeral=True,
    )

# Read introduction from a text file
with open('introduction.txt', 'r', encoding='utf-8') as file:
    introduction = file.read()

async def reconcile_active_invitation(guild: discord.Guild):
    """
    On startup, scan the announcement channel for the most recent invite message,
    then sync its current ✅ / ❔ reactions into the schedule CSV.
    """

    channel = discord.utils.get(guild.text_channels, name=ANNOUNCEMENT_CHANNEL_NAME)
    if channel is None:
        print(f"[startup] Channel #{ANNOUNCEMENT_CHANNEL_NAME} not found; skipping reconcile.")
        return

    # Find the most recent message with a date pattern
    active_message = None
    active_date = None

    async for msg in channel.history(limit=50):
        d = await _extract_date_from_message(msg)
        if d is not None:
            active_message = msg
            active_date = d
            break

    if active_message is None or active_date is None:
        print("[startup] No recent invite message found; skipping reconcile.")
        return

    # Collect current reaction states
    yes_members = []
    maybe_members = []

    for reaction in active_message.reactions:
        if str(reaction.emoji) == YES_EMOJI:
            async for user in reaction.users():
                if user.bot:
                    continue
                member = await _get_member(guild, user.id)
                if member is not None and not member.bot:
                    yes_members.append(member)

        if str(reaction.emoji) == MAYBE_EMOJI:
            async for user in reaction.users():
                if user.bot:
                    continue
                member = await _get_member(guild, user.id)
                if member is not None and not member.bot:
                    maybe_members.append(member)

    # Enforce exclusivity at the data level (YES wins if both somehow exist)
    yes_ids = {m.id for m in yes_members}
    maybe_members = [m for m in maybe_members if m.id not in yes_ids]

    attendees = ", ".join(get_alias_for_member(m) for m in yes_members)
    possible = ", ".join(get_alias_for_member(m) for m in maybe_members)

    # Update the schedule row for this date
    update_schedule_entry(
        target_date=active_date,
        status="Scheduled",
        attendees=attendees,
        possible_attendees=possible,
    )

    print(f"[startup] Reconciled invite {active_date.strftime('%m/%d/%Y')} "
          f"(yes={len(yes_members)}, maybe={len(maybe_members)}).")


@bot.event
async def on_ready():
    print("Bot is ready")
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")
    except Exception as e:
        print(f"Error in syncing commands: {e}")

    # NEW: Startup reconciliation for reactions/status on the active invitation
    try:
        # If your bot is only in one server, this is enough.
        # If it's in multiple servers, this will reconcile each.
        for g in bot.guilds:
            await reconcile_active_invitation(g)
    except Exception as e:
        print(f"[startup] Error while reconciling active invitation: {e}")


# Helper function to determine the split based on the date
def determine_split(date):
    month, day, year = map(int, date.split('/'))
    return "Split1" if 1 <= month <= 6 else "Split2"

@bot.tree.command(name='introductions')
async def hello(interaction: discord.Interaction):
    await interaction.response.send_message(f'Hello, {interaction.user.mention}! \n {introduction}', ephemeral=True)

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

@bot.tree.command(name="setalias", description="Set or update your game night alias")
async def set_alias(interaction: discord.Interaction, alias: str):
    """Allow a user to set their own display alias for scoreboards."""
    if not interaction.guild:
        await interaction.response.send_message(
            "This command can only be used in a server.", ephemeral=True
        )
        return

    member = interaction.user
    clean_alias = alias.strip()

    if not clean_alias:
        await interaction.response.send_message(
            "Please provide a non-empty alias.", ephemeral=True
        )
        return

    save_alias(member.id, clean_alias)

    await interaction.response.send_message(
        f"Alias set to **{clean_alias}** for {member.mention}.",
        ephemeral=True,
    )

@bot.tree.command(
    name="gamescore",
    description="Record a game result; uses aliases or @mentions for the current split (Game Night Council only).",
)
async def gamescore(
    interaction: discord.Interaction,
    game_name: str,
    points: float,
    winners: str,          # Winner(s)
    other_players: str,    # Other Players
    date: Optional[str] = None,
    notes: Optional[str] = None,
):
    """
    Slash-only, no-ping game scoring.

    Fields:
    - game_name: name of the game
    - points: victory points per winner
    - winners: comma-separated aliases OR @mentions for Winner(s)
      (must resolve to at least one member)
    - other_players: comma-separated aliases OR @mentions for Other Players
      (must resolve to at least one member; should not include the winners)
    - date: MM/DD/YYYY, optional (defaults to today)
    - notes: optional free text

    Winners get `points`.
    Other Players get 0.
    One row per player is appended to YEAR_SplitX.csv.
    """

    required_role_name = "Game Night Council"

    # Must be in a server
    if not interaction.guild:
        await interaction.response.send_message(
            "This command can only be used in a server.",
            ephemeral=True,
        )
        return

    # Role check
    if not any(role.name == required_role_name for role in interaction.user.roles):
        await interaction.response.send_message(
            "You do not have permission to use this command.",
            ephemeral=True,
        )
        return

    guild = interaction.guild

    # Resolve date
    if date is None or not date.strip():
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

    # Determine split & file name using your existing logic
    split = determine_split(date_str)   # e.g. "Split1" or "Split2"
    file_name = f"{year}_{split}.csv"  # e.g. "2024_Split1.csv"

    # Resolve Winner(s)
    winner_members, unresolved_winners = resolve_aliases_to_members(guild, winners)
    if not winner_members:
        await interaction.response.send_message(
            "Winner(s) field must include at least one valid alias or @mention. "
            "I could not match any of them.",
            ephemeral=True,
        )
        return

    # Resolve Other Players
    other_members, unresolved_others = resolve_aliases_to_members(guild, other_players)
    if not other_members:
        await interaction.response.send_message(
            "Other Players field must include at least one valid alias or @mention. "
            "I could not match any of them.",
            ephemeral=True,
        )
        return

    # Filter out any accidental duplicates (someone typed the same name in both fields)
    filtered_others = []
    winner_ids = {m.id for m in winner_members}
    for m in other_members:
        if m.id not in winner_ids and all(o.id != m.id for o in filtered_others):
            filtered_others.append(m)
    other_members = filtered_others

    if not other_members:
        await interaction.response.send_message(
            "All Other Players you provided are already listed as winners. "
            "Please list only non-winning players in Other Players.",
            ephemeral=True,
        )
        return

    # Handle any unresolved names
    unresolved_all = unresolved_winners + unresolved_others
    if unresolved_all:
        msg = "I couldn't match the following name(s) to any member:\n"
        msg += ", ".join(f"`{name}`" for name in unresolved_all)
        msg += (
            "\n\nPlease ensure they have an alias set with `/setalias`, "
            "or use their exact display name/username or an @mention, and then try again."
        )
        await interaction.response.send_message(msg, ephemeral=True)
        return

    # Normalise notes
    notes_text = (notes or "").strip()

    # Prepare data rows
    rows = []

    # Winners: full points
    for winner in winner_members:
        player_label = get_alias_for_member(winner)  # alias or display_name
        rows.append(
            {
                "DiscordID": str(winner.id),
                "PlayerID": player_label,
                "Score": float(points),
                "GameName": game_name,
                "Date": date_str,
                "Notes": notes_text,
            }
        )


    # Other Players: 0 points
    for other in other_members:
        player_label = get_alias_for_member(other)
        rows.append(
            {
                "DiscordID": str(other.id),
                "PlayerID": player_label,
                "Score": 0.0,
                "GameName": game_name,
                "Date": date_str,
                "Notes": notes_text,
            }
        )


    # Append to CSV (keeping columns consistent)
    # Build a dataframe for the new rows
    new_df = pd.DataFrame(rows)

    if os.path.exists(file_name):
        existing_df = pd.read_csv(file_name)

        # 1) If the old file doesn't have DiscordID, add it (blank)
        if "DiscordID" not in existing_df.columns:
            existing_df["DiscordID"] = ""

        # 2) Ensure new_df has all columns that existing_df has
        for col in existing_df.columns:
            if col not in new_df.columns:
                new_df[col] = ""

        # 3) Ensure existing_df has all columns that new_df has (future-proofing)
        for col in new_df.columns:
            if col not in existing_df.columns:
                existing_df[col] = ""

        # 4) Combine and save
        combined_df = pd.concat([existing_df, new_df], ignore_index=True)
        combined_df.to_csv(file_name, index=False)
    else:
        # First time creating the file, just write new_df
        new_df.to_csv(file_name, index=False)


    # Build a clean, public summary message (no pings)
    winners_list = ", ".join(get_alias_for_member(w) for w in winner_members)
    others_list = ", ".join(get_alias_for_member(o) for o in other_members)
    notes_display = notes_text if notes_text else "None"

    # All players list (including winners) for the output section
    all_players = winner_members + [
        o for o in other_members if all(w.id != o.id for w in winner_members)
    ]
    all_players_list = ", ".join(get_alias_for_member(p) for p in all_players)

    summary = (
        f"Score recorded for **{game_name}** on {date_str}.\n"
        f"Winner(s) ({points} pts): {winners_list}\n"
        f"Other Players (0 pts): {others_list}\n"
        f"All Players: {all_players_list}\n"
        f"Notes: {notes_display}"
    )

    await interaction.response.send_message(summary)

@bot.tree.command(
    name="schedulegamenight",
    description="Schedule a game night announcement and update the schedule CSV.",
)
async def schedule_gamenight(
    interaction: discord.Interaction,
    date: Optional[str] = None,      # MM/DD/YYYY, optional
    time: Optional[str] = None,      # e.g. "5pm", "7:30 pm", "19:00", optional
    attire: Optional[str] = None,    # default: "Any"
    location: Optional[str] = None,  # default: "Usual Location"
    notes: Optional[str] = None,     # default: "None"
):
    """
    Slash-only version of /schedulegamenight.

    - No interactive chat messages.
    - All inputs are provided as options (like /gamescore).
    - Output announcement text remains the same as before.
    """

    required_role_name = "Game Night Council"

    # Must be in a server
    if not interaction.guild:
        await interaction.response.send_message(
            "This command can only be used in a server.",
            ephemeral=True,
        )
        return

    # Role check
    if not any(role.name == required_role_name for role in interaction.user.roles):
        await interaction.response.send_message(
            "You do not have permission to use this command.",
            ephemeral=True,
        )
        return

    guild = interaction.guild

    # Find the announcement channel
    target_channel = discord.utils.get(
        guild.text_channels, name=ANNOUNCEMENT_CHANNEL_NAME
    )
    if target_channel is None:
        await interaction.response.send_message(
            f"I could not find a channel named `#{ANNOUNCEMENT_CHANNEL_NAME}`.",
            ephemeral=True,
        )
        return

    game_night_role = discord.utils.get(guild.roles, name=GAME_NIGHT_ROLE_NAME)

    # --- Resolve date/time ---

    # 1) Resolve date
    if date is None or not date.strip():
        # Use upcoming Saturday at default time as base
        base_dt = get_upcoming_saturday_at_default_time()
    else:
        try:
            dt_date = datetime.strptime(date.strip(), "%m/%d/%Y").date()
        except ValueError:
            await interaction.response.send_message(
                "Please provide the date in `MM/DD/YYYY` format, e.g. `12/07/2024`.",
                ephemeral=True,
            )
            return
        # default time 5:00 PM for this specific date
        base_dt = datetime(
            year=dt_date.year, month=dt_date.month, day=dt_date.day, hour=17, minute=0
        )

    # 2) Resolve time
    if time is None or not time.strip():
        # keep base_dt's time (5 PM if from default helper)
        final_dt = base_dt
        final_time_display = base_dt.strftime("%I:%M %p").lstrip("0")
    else:
        display, hour, minute = parse_time_input(time)
        if display is None:
            await interaction.response.send_message(
                "I couldn't understand that time. Examples: `5pm`, `7:30 pm`, `19:00`.",
                ephemeral=True,
            )
            return
        final_dt = base_dt.replace(hour=hour, minute=minute)
        final_time_display = display

    final_date_str = final_dt.strftime("%m/%d/%Y")

    # --- Resolve attire/location/notes with defaults ---

    attire_val = (attire or "").strip() or "Any"
    location_val = (location or "").strip() or "Usual Location"
    notes_val = (notes or "").strip() or "None"

    # --- Update schedule CSV for that Saturday ---

    update_schedule_entry(
        target_date=final_dt.date(),
        status="Scheduled",
        time_str=final_time_display,
        location=location_val,
        notes=notes_val,
    )

    # --- Prepare and send the announcement message (unchanged text) ---

    role_mention = game_night_role.mention if game_night_role else "@Game Night"
    message_text = (
        f"Hello {role_mention}\n"
        f"I would like to extend an invitation to another wonderful Game Night on "
        f"**{final_date_str}** at **{final_time_display}**.\n\n"
        f"Location: {location_val}\n"
        f"Attire: {attire_val}\n"
        f"Other Notes: {notes_val}\n\n"
        "I hope to see you there!\n\n"
        "*You can react to this message with*\n"
        f"{YES_EMOJI} *to confirm your attendance or*\n"
        f"{MAYBE_EMOJI} *as most likely*.\n"
        "If you select this, please let your host know beforehand if you cannot make it, "
        "so that we can save on supplies and plan appropriate games for this Game Night.\n\n"
        "Thank you,\n"
        "and we hope to see you there!"
    )

    announcement_message = await target_channel.send(message_text)

    # Auto-react so users can add to these reactions even if they can't add new ones
    await announcement_message.add_reaction(YES_EMOJI)
    await announcement_message.add_reaction(MAYBE_EMOJI)

    # Ephemeral confirmation to the command user (no clutter in channel)
    await interaction.response.send_message(
        f"Game night scheduled on **{final_date_str}** at **{final_time_display}** "
        f"and announcement posted in {target_channel.mention}.",
        ephemeral=True,
    )

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    if payload.user_id == bot.user.id:
        return

    emoji_str = str(payload.emoji)
    if emoji_str not in {YES_EMOJI, MAYBE_EMOJI}:
        return

    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return

    channel = guild.get_channel(payload.channel_id)
    if not isinstance(channel, discord.TextChannel):
        return

    if channel.name != ANNOUNCEMENT_CHANNEL_NAME:
        return

    message = await channel.fetch_message(payload.message_id)
    target_date = await _extract_date_from_message(message)
    if target_date is None:
        return

    member = payload.member or await _get_member(guild, payload.user_id)
    if member is None or member.bot:
        return

    status = "yes" if emoji_str == YES_EMOJI else "maybe"
    update_schedule_attendance_for_member(target_date, member, status)

    # Enforce one reaction at a time: remove the other if present
    other_emoji = MAYBE_EMOJI if emoji_str == YES_EMOJI else YES_EMOJI
    for reaction in message.reactions:
        if str(reaction.emoji) == other_emoji:
            async for user in reaction.users():
                if user.id == member.id:
                    await message.remove_reaction(reaction.emoji, user)
                    break


@bot.event
async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
    if payload.user_id == bot.user.id:
        return

    emoji_str = str(payload.emoji)
    if emoji_str not in {YES_EMOJI, MAYBE_EMOJI}:
        return

    guild = bot.get_guild(payload.guild_id)
    if guild is None:
        return

    channel = guild.get_channel(payload.channel_id)
    if not isinstance(channel, discord.TextChannel):
        return

    if channel.name != ANNOUNCEMENT_CHANNEL_NAME:
        return

    message = await channel.fetch_message(payload.message_id)
    target_date = await _extract_date_from_message(message)
    if target_date is None:
        return

    member = await _get_member(guild, payload.user_id)
    if member is None or member.bot:
        return

    # Determine what the member still has on the message
    has_yes = False
    has_maybe = False

    for reaction in message.reactions:
        if str(reaction.emoji) in {YES_EMOJI, MAYBE_EMOJI}:
            async for user in reaction.users():
                if user.id == member.id:
                    if str(reaction.emoji) == YES_EMOJI:
                        has_yes = True
                    else:
                        has_maybe = True

    if has_yes:
        update_schedule_attendance_for_member(target_date, member, "yes")
    elif has_maybe:
        update_schedule_attendance_for_member(target_date, member, "maybe")
    else:
        update_schedule_attendance_for_member(target_date, member, "none")


async def _extract_date_from_message(message: discord.Message) -> Optional[date]:
    """Find the first MM/DD/YYYY date in the message content."""
    match = re.search(r"\b(\d{2}/\d{2}/\d{4})\b", message.content)
    if not match:
        return None
    try:
        dt = datetime.strptime(match.group(1), "%m/%d/%Y")
        return dt.date()
    except ValueError:
        return None

async def _get_member(guild: discord.Guild, user_id: int) -> Optional[discord.Member]:
    """Get member from cache or fetch from API."""
    member = guild.get_member(user_id)
    if member is not None:
        return member
    try:
        return await guild.fetch_member(user_id)
    except Exception:
        return None


bot_token = os.getenv('DISCORD_BOT_TOKEN')
bot.run(bot_token)
