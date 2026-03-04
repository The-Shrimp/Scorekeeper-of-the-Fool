"""
schedule.py (Polars)
Split schedule CSV creation and updates (no pandas/numpy).
"""

import os
import calendar
import polars as pl
from datetime import datetime, date, timedelta
import discord

from constants import (
    GAME_NIGHT_ROLE_NAME,
    ANNOUNCEMENT_CHANNEL_NAME,
    YES_EMOJI,
    MAYBE_EMOJI,
    NO_EMOJI,
    COUNCIL_ROLE_NAME,
)
from utils_split import determine_split, schedule_filename_for
from utils_time import get_upcoming_saturday_at_default_time, parse_time_input
from aliases import get_alias_for_member
from split_ids import split_id_for_date
import db

SCHEDULE_COLUMNS = [
    "Date", "Status", "Time", "Location", "Notes",
    "Attendees", "Possible Attendees", "Unavailable", "Afterwards Comments"
]

def _empty_schedule_df() -> pl.DataFrame:
    return pl.DataFrame({c: pl.Series([], dtype=pl.Utf8) for c in SCHEDULE_COLUMNS})

def generate_saturdays_for_split(year: int, split: str):
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
    date_str = target_date.strftime("%m/%d/%Y")
    split = determine_split(date_str)
    file_name = schedule_filename_for(target_date)

    if os.path.exists(file_name):
        try:
            df = pl.read_csv(file_name, dtypes={c: pl.Utf8 for c in SCHEDULE_COLUMNS}, infer_schema_length=0)
        except Exception:
            df = _empty_schedule_df()
    else:
        df = _empty_schedule_df()

    # Ensure all columns exist
    for c in SCHEDULE_COLUMNS:
        if c not in df.columns:
            df = df.with_columns(pl.lit("").cast(pl.Utf8).alias(c))

    existing_dates = set(df["Date"].to_list()) if df.height > 0 else set()

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
                "Unavailable": "",
                "Afterwards Comments": "",
            })

    if new_rows:
        df = pl.concat([df, pl.DataFrame(new_rows)], how="vertical")

    if df.height > 0:
        # Deduplicate by Date (keep first)
        df = df.unique(subset=["Date"], keep="first")
        # Sort by actual date
        df = df.with_columns(
            pl.col("Date").str.strptime(pl.Date, format="%m/%d/%Y", strict=False).alias("_sort_date")
        ).sort("_sort_date").drop("_sort_date")

    df.write_csv(file_name)
    return file_name

def update_schedule_entry(
    target_date: date,
    status: str = None,
    time_str: str = None,
    location: str = None,
    notes: str = None,
    attendees: str = None,
    possible_attendees: str = None,
    unavailable: str = None,
    afterwards_comments: str = None,
):
    schedule_file = ensure_schedule_file_for_date(target_date)
    df = pl.read_csv(schedule_file, dtypes={c: pl.Utf8 for c in SCHEDULE_COLUMNS}, infer_schema_length=0)

    date_str = target_date.strftime("%m/%d/%Y")
    if df.filter(pl.col("Date") == date_str).height == 0:
        df = pl.concat([df, pl.DataFrame([{
            "Date": date_str,
            "Status": "Undecided",
            "Time": "5 PM",
            "Location": "Usual Location",
            "Notes": "None",
            "Attendees": "",
            "Possible Attendees": "",
            "Unavailable": "",
            "Afterwards Comments": "",
        }])], how="vertical")

    def _set(col, val):
        nonlocal df
        if val is None:
            return
        df = df.with_columns(
            pl.when(pl.col("Date") == date_str).then(pl.lit(str(val))).otherwise(pl.col(col)).alias(col)
        )

    _set("Status", status)
    _set("Time", time_str)
    _set("Location", location)
    _set("Notes", notes)
    _set("Attendees", attendees)
    _set("Possible Attendees", possible_attendees)
    _set("Unavailable", unavailable)
    _set("Afterwards Comments", afterwards_comments)

    df.write_csv(schedule_file)

    # Sync to DB
    date_iso = target_date.strftime("%Y-%m-%d")
    db.upsert_game_night(
        date_iso=date_iso,
        split_id=split_id_for_date(target_date),
        status=status,
        time_str=time_str,
        location=location,
        notes=notes,
        afterwards_comments=afterwards_comments,
    )

def parse_list_field(text: str) -> list:
    if not isinstance(text, str) or not text.strip():
        return []
    return [x.strip() for x in text.split(",") if x.strip()]

def join_list_field(items: list) -> str:
    # preserve stable order by sorting; remove duplicates
    return ", ".join(sorted(set(items)))

def update_schedule_attendance_for_member(target_date: date, member: discord.Member, status: str):
    schedule_file = ensure_schedule_file_for_date(target_date)
    df = pl.read_csv(schedule_file, dtypes={c: pl.Utf8 for c in SCHEDULE_COLUMNS}, infer_schema_length=0)

    date_str = target_date.strftime("%m/%d/%Y")
    row = df.filter(pl.col("Date") == date_str)
    if row.height == 0:
        return

    attendees = parse_list_field(row["Attendees"][0])
    maybes = parse_list_field(row["Possible Attendees"][0])
    unavailables = parse_list_field(row["Unavailable"][0] if "Unavailable" in row.columns else "")
    label = get_alias_for_member(member)

    attendees = [x for x in attendees if x != label]
    maybes = [x for x in maybes if x != label]
    unavailables = [x for x in unavailables if x != label]

    if status == "yes":
        attendees.append(label)
    elif status == "maybe":
        maybes.append(label)
    elif status == "unavailable":
        unavailables.append(label)

    attendees_str = join_list_field(attendees)
    maybes_str = join_list_field(maybes)
    unavailables_str = join_list_field(unavailables)

    df = df.with_columns(
        pl.when(pl.col("Date") == date_str).then(pl.lit(attendees_str)).otherwise(pl.col("Attendees")).alias("Attendees"),
        pl.when(pl.col("Date") == date_str).then(pl.lit(maybes_str)).otherwise(pl.col("Possible Attendees")).alias("Possible Attendees"),
        pl.when(pl.col("Date") == date_str).then(pl.lit(unavailables_str)).otherwise(pl.col("Unavailable")).alias("Unavailable"),
    )
    df.write_csv(schedule_file)

    # Sync RSVP and game night to DB
    date_iso = target_date.strftime("%Y-%m-%d")
    db.upsert_game_night(date_iso=date_iso, split_id=split_id_for_date(target_date))
    db.upsert_rsvp(date_iso=date_iso, discord_id=str(member.id), rsvp_status=status)


def sync_schedule_attendance_snapshot(
    target_date: date,
    yes_members: list[discord.Member],
    maybe_members: list[discord.Member],
    unavailable_members: list[discord.Member],
) -> None:
    """
    Replace the schedule CSV and RSVP DB snapshot for a date from the authoritative invite state.
    Used during startup reconciliation when the bot needs to catch up after downtime.
    """
    attendees = join_list_field([get_alias_for_member(m) for m in yes_members])
    possible_attendees = join_list_field([get_alias_for_member(m) for m in maybe_members])
    unavailable = join_list_field([get_alias_for_member(m) for m in unavailable_members])

    update_schedule_entry(
        target_date=target_date,
        status="Scheduled",
        attendees=attendees,
        possible_attendees=possible_attendees,
        unavailable=unavailable,
    )

    status_map: dict[str, str] = {}
    for member in yes_members:
        status_map[str(member.id)] = "yes"
    for member in maybe_members:
        status_map[str(member.id)] = "maybe"
    for member in unavailable_members:
        status_map[str(member.id)] = "unavailable"

    db.replace_rsvps_for_night(target_date.strftime("%Y-%m-%d"), status_map)

def _has_council_role(member: discord.Member) -> bool:
    return any(r.name == COUNCIL_ROLE_NAME for r in getattr(member, "roles", []))

def register(bot):
    @bot.tree.command(name="schedulegamenight", description="Schedule a game night announcement and update the schedule CSV.")
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
            await interaction.response.send_message(f"I could not find a channel named `#{ANNOUNCEMENT_CHANNEL_NAME}`.", ephemeral=True)
            return

        role = discord.utils.get(guild.roles, name=GAME_NIGHT_ROLE_NAME)

        # Default date = upcoming Saturday at 5pm
        if not date or not date.strip():
            base_dt = get_upcoming_saturday_at_default_time()
        else:
            try:
                dt_date = datetime.strptime(date.strip(), "%m/%d/%Y").date()
            except ValueError:
                await interaction.response.send_message("Please provide the date in `MM/DD/YYYY` format.", ephemeral=True)
                return
            base_dt = datetime(dt_date.year, dt_date.month, dt_date.day, 17, 0)

        # Default time = 5pm, else parse
        if not time or not time.strip():
            final_dt = base_dt
            final_time_display = base_dt.strftime("%I:%M %p").lstrip("0")
        else:
            display, hour, minute = parse_time_input(time)
            if display is None:
                await interaction.response.send_message("Time examples: `5pm`, `7:30 pm`, `19:00`.", ephemeral=True)
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
            f"I would like to extend an invitation to another wonderful Game Night on **{final_date_str}** at **{final_time_display}**\n\n"
            f"Location: {location_val}\n"
            f"Attire: {attire_val}\n"
            f"Other Notes: {notes_val}\n\n"
            "I hope to see you there!\n\n"
            "*You can react to this message with*\n"
            f"{YES_EMOJI} *to confirm your attendance,*\n"
            f"{MAYBE_EMOJI} *if you're not sure yet, or*\n"
            f"{NO_EMOJI} *if you cannot make it.*\n"
            "If you are unsure, please let your host know beforehand if you cannot make it, "
            "so that we can save on supplies and plan appropriate games for this Game Night.\n\n"
            "Thank you,\n"
            "and we hope to see you there!"
        )

        msg = await target_channel.send(message_text)
        await msg.add_reaction(YES_EMOJI)
        await msg.add_reaction(MAYBE_EMOJI)
        await msg.add_reaction(NO_EMOJI)

        await interaction.response.send_message(
            f"Game night scheduled on **{final_date_str}** at **{final_time_display}** and posted in {target_channel.mention}.",
            ephemeral=True,
        )

    @bot.tree.command(name="nightstatus", description="Show RSVP and attendance status for a game night (Council only).")
    async def nightstatus(interaction: discord.Interaction, date: str):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        if not _has_council_role(interaction.user):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        try:
            target_date = datetime.strptime(date.strip(), "%m/%d/%Y").date()
        except ValueError:
            await interaction.response.send_message("Please provide the date in `MM/DD/YYYY` format.", ephemeral=True)
            return

        date_iso = target_date.strftime("%Y-%m-%d")
        night = db.get_game_night_by_date(date_iso)
        rsvps = db.get_rsvps_for_night(date_iso)
        derived = db.get_derived_attendance_for_night(date_iso)

        if not night and not rsvps and not derived:
            await interaction.response.send_message(
                f"No game-night data found for {target_date.strftime('%m/%d/%Y')}.",
                ephemeral=True,
            )
            return

        yes_count = sum(1 for r in rsvps if r["rsvp_status"] == "yes")
        maybe_count = sum(1 for r in rsvps if r["rsvp_status"] == "maybe")
        unavailable_count = sum(1 for r in rsvps if r["rsvp_status"] == "unavailable")
        derived_ids = sorted({str(r["discord_id"]) for r in derived})

        lines = [
            f"Date: {target_date.strftime('%m/%d/%Y')}",
            f"Status: {night['status'] if night else 'Undecided'}",
            f"Time: {night['time_str'] if night and night.get('time_str') else 'N/A'}",
            f"Location: {night['location'] if night and night.get('location') else 'N/A'}",
            f"RSVP Yes: {yes_count}",
            f"RSVP Maybe: {maybe_count}",
            f"RSVP No: {unavailable_count}",
            f"Derived Attendance: {len(derived_ids)}",
        ]

        if derived_ids:
            names = []
            for did in derived_ids:
                alias = db.get_alias(int(did))
                if alias:
                    names.append(alias)
                    continue
                member = interaction.guild.get_member(int(did))
                names.append(member.display_name if member else f"User({did})")
            lines.append(f"Attended: {', '.join(names)}")

        await interaction.response.send_message("```" + "\n".join(lines) + "```", ephemeral=True)
