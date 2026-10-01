"""
announcements.py
Embed builder and live-refresh system for game night announcement messages.

refresh_announcement edits the stored Discord message in-place.
queue_refresh debounces rapid updates (e.g. multiple reactions firing at once).
"""

import asyncio
from datetime import datetime
from typing import Optional

import discord

import db
from public_identity import display_name_for_discord
from privacy import is_member_only_channel
from constants import YES_EMOJI, MAYBE_EMOJI, NO_EMOJI

# date_iso -> asyncio.Task
_pending_refreshes: dict[str, asyncio.Task] = {}


def _date_display(date_iso: str) -> str:
    try:
        dt = datetime.strptime(date_iso, "%Y-%m-%d")
        return f"{dt.strftime('%A, %B')} {dt.day}, {dt.year}"
    except Exception:
        return date_iso


def _resolve_name_from_db(discord_id: str) -> str:
    alias = db.get_alias(int(discord_id))
    return display_name_for_discord(int(discord_id), alias)


def build_announcement_embed(
    night_row: dict,
    rsvps: list[dict],
    scorekeeper_name: Optional[str] = None,
) -> discord.Embed:
    date_iso = night_row["date_iso"]
    status = night_row.get("status") or "Undecided"
    time_str = night_row.get("time_str") or "TBD"
    location = night_row.get("location") or "Private details are available through Discord."
    notes = night_row.get("notes") or "None"
    date_label = _date_display(date_iso)

    if status == "Cancelled":
        title = f"\U0001f6ab Game Night \u2014 {date_label} (CANCELLED)"
        color = discord.Color.red()
    elif status == "Scheduled":
        title = f"\U0001f3b2 Game Night \u2014 {date_label}"
        color = discord.Color.green()
    else:
        title = f"\U0001f3b2 Game Night \u2014 {date_label}"
        color = discord.Color.orange()

    embed = discord.Embed(title=title, color=color)

    # Try to embed a timestamp from date + time
    try:
        dt = datetime.strptime(date_iso, "%Y-%m-%d")
        for fmt in ("%I:%M %p", "%I %p", "%H:%M", "%I:%M%p", "%I%p"):
            try:
                t = datetime.strptime(time_str.strip().upper(), fmt)
                embed.timestamp = dt.replace(hour=t.hour, minute=t.minute)
                break
            except ValueError:
                continue
    except Exception:
        pass

    # Core fields
    embed.add_field(name="Date", value=date_label, inline=True)
    embed.add_field(name="Time", value=time_str, inline=True)
    embed.add_field(name="Location", value=location, inline=True)

    if notes and notes.strip().lower() not in ("none", ""):
        embed.add_field(name="Notes", value=notes, inline=False)

    embed.add_field(name="Scorekeeper", value=scorekeeper_name or "TBD", inline=True)

    # RSVP counts
    yes_list = [r for r in rsvps if r["rsvp_status"] == "yes"]
    maybe_list = [r for r in rsvps if r["rsvp_status"] == "maybe"]
    no_list = [r for r in rsvps if r["rsvp_status"] == "unavailable"]

    embed.add_field(
        name="RSVPs",
        value=f"{YES_EMOJI} {len(yes_list)}   {MAYBE_EMOJI} {len(maybe_list)}   {NO_EMOJI} {len(no_list)}",
        inline=True,
    )

    # Attendee names (yes only, first 8)
    if yes_list:
        names = [_resolve_name_from_db(r["discord_id"]) for r in yes_list]
        if len(names) <= 8:
            attendee_str = ", ".join(names)
        else:
            attendee_str = ", ".join(names[:8]) + f" +{len(names) - 8} more"
        embed.add_field(name="Attending", value=attendee_str, inline=False)

    embed.set_footer(
        text=f"React below to RSVP  \u2022  {YES_EMOJI} Attending  {MAYBE_EMOJI} Maybe  {NO_EMOJI} Can't make it"
    )

    return embed


async def refresh_announcement(bot: discord.Client, date_iso: str) -> None:
    night_row = db.get_game_night_by_date(date_iso)
    if night_row is None:
        return

    channel_id_str, message_id_str = db.get_announcement_ids(date_iso)
    if not channel_id_str or not message_id_str:
        return

    try:
        channel = bot.get_channel(int(channel_id_str)) or await bot.fetch_channel(int(channel_id_str))
    except Exception as exc:
        print(f"[announce] Could not fetch channel {channel_id_str}: {exc}")
        return

    if not is_member_only_channel(channel):
        print("[announcements] Refused private announcement in an unverified channel.")
        return

    try:
        message = await channel.fetch_message(int(message_id_str))
    except discord.NotFound:
        print(f"[announce] Message for {date_iso} not found; clearing stored IDs.")
        db.save_announcement_ids(date_iso, None, None)
        return
    except Exception as exc:
        print(f"[announce] Could not fetch message {message_id_str}: {exc}")
        return

    rsvps = db.get_rsvps_for_night(date_iso)

    scorekeeper_id = night_row.get("scorekeeper_discord_id")
    scorekeeper_name: Optional[str] = None
    if scorekeeper_id:
        scorekeeper_name = display_name_for_discord(int(scorekeeper_id), db.get_alias(int(scorekeeper_id)))

    embed = build_announcement_embed(night_row, rsvps, scorekeeper_name)

    try:
        await message.edit(embed=embed)
    except discord.Forbidden:
        print(f"[announce] Missing permissions to edit announcement for {date_iso}.")
    except Exception as exc:
        print(f"[announce] Failed to edit announcement for {date_iso}: {exc}")


async def _delayed_refresh(bot: discord.Client, date_iso: str, delay: float) -> None:
    await asyncio.sleep(delay)
    await refresh_announcement(bot, date_iso)
    _pending_refreshes.pop(date_iso, None)


async def queue_refresh(bot: discord.Client, date_iso: str, delay: float = 3.0) -> None:
    existing = _pending_refreshes.get(date_iso)
    if existing and not existing.done():
        existing.cancel()
    task = asyncio.create_task(_delayed_refresh(bot, date_iso, delay))
    _pending_refreshes[date_iso] = task
