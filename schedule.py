"""
schedule.py (Polars)
Split schedule CSV creation and updates (no pandas/numpy).
"""

import os
import calendar
import polars as pl
from datetime import datetime, date, timedelta
from typing import Optional
import discord

from discord.ext import tasks
from utils_split import determine_split, schedule_filename_for
from utils_time import get_upcoming_saturday_at_default_time, parse_time_input
from aliases import get_alias_for_member
from split_ids import split_id_for_date
from constants import (
    GAME_NIGHT_ROLE_NAME,
    ANNOUNCEMENT_CHANNEL_NAME,
    YES_EMOJI,
    MAYBE_EMOJI,
    NO_EMOJI,
    COUNCIL_ROLE_NAME,
    REMINDER_DAY,
    REMINDER_HOUR,
    REMINDER_TIMEZONE,
)
import db
from privacy import is_authorized_guild, is_member_only_channel

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
                "Location": "Private details are available through Discord.",
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
            "Location": "Private details are available through Discord.",
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

def _resolve_date(date_str: Optional[str]) -> Optional[datetime]:
    """Return a datetime for the given MM/DD/YYYY string, or the upcoming Saturday if None."""
    if not date_str or not date_str.strip():
        return get_upcoming_saturday_at_default_time()
    try:
        d = datetime.strptime(date_str.strip(), "%m/%d/%Y").date()
        return datetime(d.year, d.month, d.day, 17, 0)
    except ValueError:
        return None


class _ReannounceView(discord.ui.View):
    """Two-button view shown when an announcement already exists for a date."""

    def __init__(self, bot, date_iso, final_dt, final_time_display, attire_val, location_val, notes_val, target_channel, role):
        super().__init__(timeout=60)
        self.bot = bot
        self.date_iso = date_iso
        self.final_dt = final_dt
        self.final_time_display = final_time_display
        self.attire_val = attire_val
        self.location_val = location_val
        self.notes_val = notes_val
        self.target_channel = target_channel
        self.role = role

    @discord.ui.button(label="Edit existing", style=discord.ButtonStyle.primary)
    async def edit_existing(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_authorized_guild(interaction.guild) or not _has_council_role(interaction.user) or not is_member_only_channel(self.target_channel):
            await interaction.response.send_message("Private scheduling authorization is required.", ephemeral=True)
            return
        from announcements import refresh_announcement
        update_schedule_entry(
            target_date=self.final_dt.date(),
            status="Scheduled",
            time_str=self.final_time_display,
            location=self.location_val,
            notes=self.notes_val,
        )
        await refresh_announcement(self.bot, self.date_iso)
        await interaction.response.edit_message(
            content=f"Existing announcement updated for **{self.final_dt.strftime('%m/%d/%Y')}**.",
            view=None,
        )

    @discord.ui.button(label="Post new", style=discord.ButtonStyle.secondary)
    async def post_new(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not is_authorized_guild(interaction.guild) or not _has_council_role(interaction.user) or not is_member_only_channel(self.target_channel):
            await interaction.response.send_message("Private scheduling authorization is required.", ephemeral=True)
            return
        from announcements import build_announcement_embed, refresh_announcement
        from scorekeeper import assign_scorekeeper
        update_schedule_entry(
            target_date=self.final_dt.date(),
            status="Scheduled",
            time_str=self.final_time_display,
            location=self.location_val,
            notes=self.notes_val,
        )
        night_row = db.get_game_night_by_date(self.date_iso)
        embed = build_announcement_embed(night_row or {}, [], None)
        role_mention = self.role.mention if self.role else "@Game Night"
        msg = await self.target_channel.send(
            content=f"{role_mention} \u2014 {self.final_dt.strftime('%m/%d/%Y')}",
            embed=embed,
        )
        await msg.add_reaction(YES_EMOJI)
        await msg.add_reaction(MAYBE_EMOJI)
        await msg.add_reaction(NO_EMOJI)
        db.save_announcement_ids(self.date_iso, str(self.target_channel.id), str(msg.id))
        await assign_scorekeeper(self.bot, interaction.guild, self.date_iso)
        await refresh_announcement(self.bot, self.date_iso)
        await interaction.response.edit_message(
            content=f"New announcement posted in {self.target_channel.mention}.",
            view=None,
        )


def register(bot):
    @bot.tree.command(name="schedulegamenight", description="Schedule a game night announcement (Council only).")
    async def schedule_gamenight(
        interaction: discord.Interaction,
        date: str = None,
        time: str = None,
        attire: str = None,
        location: str = None,
        notes: str = None,
    ):
        if not is_authorized_guild(interaction.guild):
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        if not _has_council_role(interaction.user):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        guild = interaction.guild
        target_channel = discord.utils.get(guild.text_channels, name=ANNOUNCEMENT_CHANNEL_NAME)
        if target_channel is None:
            await interaction.response.send_message(f"Could not find `#{ANNOUNCEMENT_CHANNEL_NAME}`.", ephemeral=True)
            return

        if not is_member_only_channel(target_channel):
            await interaction.response.send_message("Private scheduling requires a verified member-only channel.", ephemeral=True)
            return

        role = discord.utils.get(guild.roles, name=GAME_NIGHT_ROLE_NAME)

        base_dt = _resolve_date(date)
        if base_dt is None:
            await interaction.response.send_message("Please provide the date in `MM/DD/YYYY` format.", ephemeral=True)
            return

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
        date_iso = final_dt.strftime("%Y-%m-%d")
        attire_val = (attire or "").strip() or "Any"
        location_val = (location or "").strip() or "Private details are available through Discord."
        notes_val = (notes or "").strip() or "None"

        update_schedule_entry(
            target_date=final_dt.date(),
            status="Scheduled",
            time_str=final_time_display,
            location=location_val,
            notes=notes_val,
        )

        _, existing_msg_id = db.get_announcement_ids(date_iso)
        if existing_msg_id:
            view = _ReannounceView(bot, date_iso, final_dt, final_time_display,
                                   attire_val, location_val, notes_val, target_channel, role)
            await interaction.response.send_message(
                f"An announcement already exists for **{final_date_str}**. What would you like to do?",
                view=view,
                ephemeral=True,
            )
            return

        # No existing announcement — post fresh embed
        from announcements import build_announcement_embed, refresh_announcement
        from scorekeeper import assign_scorekeeper
        night_row = db.get_game_night_by_date(date_iso)
        embed = build_announcement_embed(night_row or {}, [], None)
        role_mention = role.mention if role else "@Game Night"
        msg = await target_channel.send(
            content=f"{role_mention} \u2014 {final_date_str}",
            embed=embed,
        )
        await msg.add_reaction(YES_EMOJI)
        await msg.add_reaction(MAYBE_EMOJI)
        await msg.add_reaction(NO_EMOJI)
        db.save_announcement_ids(date_iso, str(target_channel.id), str(msg.id))
        await assign_scorekeeper(bot, guild, date_iso)
        await refresh_announcement(bot, date_iso)

        await interaction.response.send_message(
            f"Game night scheduled on **{final_date_str}** at **{final_time_display}** and posted in {target_channel.mention}.",
            ephemeral=True,
        )

    @bot.tree.command(name="cancel_gamenight", description="Cancel a game night and update the announcement embed (Council only).")
    async def cancel_gamenight(
        interaction: discord.Interaction,
        date: str = None,
    ):
        if not is_authorized_guild(interaction.guild):
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        if not _has_council_role(interaction.user):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        base_dt = _resolve_date(date)
        if base_dt is None:
            await interaction.response.send_message("Please provide the date in `MM/DD/YYYY` format.", ephemeral=True)
            return

        final_date_str = base_dt.strftime("%m/%d/%Y")
        date_iso = base_dt.strftime("%Y-%m-%d")

        update_schedule_entry(target_date=base_dt.date(), status="Cancelled")

        from announcements import refresh_announcement
        await refresh_announcement(bot, date_iso)

        await interaction.response.send_message(
            f"Game night on **{final_date_str}** marked as Cancelled. The announcement embed has been updated.",
            ephemeral=True,
        )

    @bot.tree.command(name="nightstatus", description="Show RSVP and attendance status for a game night (Council only).")
    async def nightstatus(interaction: discord.Interaction, date: str):
        if not is_authorized_guild(interaction.guild):
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
                from public_identity import display_name_for_discord
                names.append(display_name_for_discord(int(did), db.get_alias(int(did))))
            lines.append(f"Attended: {', '.join(names)}")

        await interaction.response.send_message("```" + "\n".join(lines) + "```", ephemeral=True)

    @bot.tree.command(name="remind_maybes", description="Send reminder DMs to maybe RSVPs for the upcoming game night (Council only).")
    async def remind_maybes(
        interaction: discord.Interaction,
        date: str = None,
        force: bool = False,
    ):
        if not is_authorized_guild(interaction.guild):
            await interaction.response.send_message("Server only.", ephemeral=True)
            return
        if not _has_council_role(interaction.user):
            await interaction.response.send_message("You do not have permission.", ephemeral=True)
            return

        base_dt = _resolve_date(date)
        if base_dt is None:
            await interaction.response.send_message("Please provide the date in `MM/DD/YYYY` format.", ephemeral=True)
            return

        date_iso = base_dt.strftime("%Y-%m-%d")
        await interaction.response.defer(ephemeral=True)
        sent = await _send_maybe_reminders_for_date(bot, interaction.guild, date_iso, force=force)
        await interaction.followup.send(
            f"Sent reminder DMs to {sent} maybe RSVP(s) for {base_dt.strftime('%m/%d/%Y')}.",
            ephemeral=True,
        )


async def _send_maybe_reminders_for_date(
    bot: discord.Client,
    guild: discord.Guild,
    date_iso: str,
    force: bool = False,
) -> int:
    """DM all maybe RSVPs for a date. Returns count of DMs sent."""
    if force:
        db.clear_maybe_reminders_for_night(date_iso)

    night_row = db.get_game_night_by_date(date_iso)
    time_str = (night_row.get("time_str") or "TBD") if night_row else "TBD"
    date_display = date_iso

    try:
        dt = datetime.strptime(date_iso, "%Y-%m-%d")
        date_display = f"{dt.strftime('%B')} {dt.day}, {dt.year}"
    except Exception:
        pass

    rsvps = db.get_rsvps_for_night(date_iso)
    maybe_ids = [r["discord_id"] for r in rsvps if r["rsvp_status"] == "maybe"]

    sent = 0
    for discord_id in maybe_ids:
        if not db.record_maybe_reminder_if_new(date_iso, discord_id):
            continue
        try:
            member = guild.get_member(int(discord_id)) or await guild.fetch_member(int(discord_id))
        except Exception:
            continue
        try:
            await member.send(
                f"{MAYBE_EMOJI} **Game Night reminder \u2014 {date_display} at {time_str}**\n\n"
                "You marked yourself as maybe for this week's game night. "
                "We'd love to know if you're coming so we can plan appropriately!\n\n"
                f"React to the announcement in #{ANNOUNCEMENT_CHANNEL_NAME} to update your RSVP:\n"
                f"{YES_EMOJI} Attending   {NO_EMOJI} Can't make it\n\n"
                "Hope to see you there!"
            )
            sent += 1
        except Exception as exc:
            print(f"[remind] Failed to DM {discord_id}: {exc}")

    return sent


@tasks.loop(minutes=60)
async def send_maybe_reminders(bot: discord.Client) -> None:
    """Hourly task — fires Thursday at 7 PM local time."""
    try:
        from zoneinfo import ZoneInfo
        import datetime as _dt
        tz = ZoneInfo(REMINDER_TIMEZONE)
        now = _dt.datetime.now(tz)
        if now.weekday() != REMINDER_DAY or now.hour != REMINDER_HOUR:
            return
    except Exception as exc:
        print(f"[remind] Timezone check failed: {exc}")
        return

    sat_dt = get_upcoming_saturday_at_default_time()
    date_iso = sat_dt.strftime("%Y-%m-%d")

    for guild in bot.guilds:
        sent = await _send_maybe_reminders_for_date(bot, guild, date_iso)
        if sent:
            print(f"[remind] Sent {sent} maybe reminder(s) for {date_iso}.")
