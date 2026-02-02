"""
schedule.py
Schedule CSV creation and updates.

Purpose:
- Maintain one schedule CSV per split.
- Ensure every Saturday exists as a row.
- Update a specific Saturday’s details.
- Maintain Attendees / Possible Attendees lists.

Best practice:
- All schedule CSV reads/writes live here, so changes are localized.
"""

import os
import calendar
import pandas as pd
from datetime import datetime, date, timedelta
import discord

from constants import GAME_NIGHT_ROLE_NAME, ANNOUNCEMENT_CHANNEL_NAME, YES_EMOJI, MAYBE_EMOJI, COUNCIL_ROLE_NAME
from utils_split import determine_split, schedule_filename_for
from utils_time import get_upcoming_saturday_at_default_time, parse_time_input
from aliases import get_alias_for_member

SCHEDULE_COLUMNS = [
    "Date", "Status", "Time", "Location", "Notes",
    "Attendees", "Possible Attendees", "Afterwards Comments"
]

def generate_saturdays_for_split(year: int, split: str):
    """Yield all Saturdays in the given year's split."""
    if split == "Split1":
        start_month, end_month = 1, 6
    else:
        start_month, end_month = 7, 12

    start_date = date(year, start_month, 1)
    end_date = date(year, end_month, calendar.monthrange(year, end_month)[1])

    cur = start_date
    while cur.weekday() != 5:
        cur += timedelta(days=1)

    while cur <= end_date:
        yield cur
        cur += timedelta(days=7)

def ensure_schedule_file_for_date(target_date: date) -> str:
    """Ensure the split schedule file exists and contains all Saturdays in that split."""
    date_str = target_date.strftime("%m/%d/%Y")
    split = determine_split(date_str)
    file_name = schedule_filename_for(target_date)

    if os.path.exists(file_name):
        df = pd.read_csv(file_name, dtype=str)
    else:
        df = pd.DataFrame(columns=SCHEDULE_COLUMNS)

    existing_dates = set(df["Date"].tolist()) if "Date" in df.columns else set()

    new_rows = []
    for sat in generate_saturdays_for_split(target_date.year, split):
        sat_str = sat.strftime("%m/%d/%Y")
        if sat_str not in existing_dates:
            new_rows.append({
                "Date": sat_str,
                "Status": "Undecided",
                "Time": "5 PM",
                "Location": "Usual Location",
                "Notes": "None",
                "Attendees": "",
                "Possible Attendees": "",
                "Afterwards Comments": "",
            })

    if new_rows:
        df = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)

    if not df.empty:
        df = df.drop_duplicates(subset=["Date"])
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
        df = pd.concat([df, pd.DataFrame([{
            "Date": date_str,
            "Status": "Undecided",
            "Time": "5 PM",
            "Location": "Usual Location",
            "Notes": "None",
            "Attendees": "",
            "Possible Attendees": "",
            "Afterwards Comments": "",
        }])], ignore_index=True)
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
    if not isinstance(text, str) or not text.strip():
        return []
    return [x.strip() for x in text.split(",") if x.strip()]

def join_list_field(items: list) -> str:
    return ", ".join(sorted(set(items)))

def update_schedule_attendance_for_member(target_date: date, member: discord.Member, status: str):
    """status: 'yes', 'maybe', 'none'"""
    schedule_file = ensure_schedule_file_for_date(target_date)
    df = pd.read_csv(schedule_file, dtype=str)
    date_str = target_date.strftime("%m/%d/%Y")
    mask = df["Date"] == date_str
    if not mask.any():
        return

    idx = df.index[mask][0]
    attendees = parse_list_field(df.at[idx, "Attendees"])
    maybes = parse_list_field(df.at[idx, "Possible Attendees"])
    label = get_alias_for_member(member)

    attendees = [x for x in attendees if x != label]
    maybes = [x for x in maybes if x != label]

    if status == "yes":
        attendees.append(label)
    elif status == "maybe":
        maybes.append(label)

    df.at[idx, "Attendees"] = join_list_field(attendees)
    df.at[idx, "Possible Attendees"] = join_list_field(maybes)
    df.to_csv(schedule_file, index=False)

def _has_council_role(member: discord.Member) -> bool:
    return any(r.name == COUNCIL_ROLE_NAME for r in getattr(member, "roles", []))

def register(bot):
    @bot.tree.command(
        name="schedulegamenight",
        description="Schedule a game night announcement and update the schedule CSV.",
    )
    async def schedule_gamenight(
        interaction: discord.Interaction,
        date: str = None,
        time: str = None,
        attire: str = None,
        location: str = None,
        notes: str = None,
    ):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        if not _has_council_role(interaction.user):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        guild = interaction.guild
        target_channel = discord.utils.get(guild.text_channels, name=ANNOUNCEMENT_CHANNEL_NAME)
        if target_channel is None:
            await interaction.response.send_message(
                f"I could not find a channel named `#{ANNOUNCEMENT_CHANNEL_NAME}`.",
                ephemeral=True,
            )
            return

        role = discord.utils.get(guild.roles, name=GAME_NIGHT_ROLE_NAME)

        if not date or not date.strip():
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
            base_dt = datetime(dt_date.year, dt_date.month, dt_date.day, 17, 0)

        if not time or not time.strip():
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
        attire_val = (attire or "").strip() or "Any"
        location_val = (location or "").strip() or "Usual Location"
        notes_val = (notes or "").strip() or "None"

        update_schedule_entry(
            target_date=final_dt.date(),
            status="Scheduled",
            time_str=final_time_display,
            location=location_val,
            notes=notes_val,
        )

        role_mention = role.mention if role else "@Game Night"
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

        msg = await target_channel.send(message_text)
        await msg.add_reaction(YES_EMOJI)
        await msg.add_reaction(MAYBE_EMOJI)

        await interaction.response.send_message(
            f"Game night scheduled on **{final_date_str}** at **{final_time_display}** "
            f"and announcement posted in {target_channel.mention}.",
            ephemeral=True,
        )
