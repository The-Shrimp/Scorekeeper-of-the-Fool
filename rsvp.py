"""
rsvp.py
Reaction handlers + startup reconciliation.

Why this module matters:
- Your bot isn't always online, so it must "catch up" when it starts.
- Reaction events update the schedule CSV live when online.
- reconcile_active_invitation() reads the latest invite message and syncs attendees.

Best practice:
- Keep Discord event listeners isolated (easy to audit, easy to debug).
"""

import re
import discord
from datetime import datetime, date
from typing import Optional

from constants import YES_EMOJI, MAYBE_EMOJI, NO_EMOJI, ANNOUNCEMENT_CHANNEL_NAME
from schedule import update_schedule_attendance_for_member, update_schedule_entry
from aliases import get_alias_for_member

async def _extract_date_from_message(message: discord.Message) -> Optional[date]:
    match = re.search(r"\b(\d{2}/\d{2}/\d{4})\b", message.content)
    if not match:
        return None
    try:
        dt = datetime.strptime(match.group(1), "%m/%d/%Y")
        return dt.date()
    except ValueError:
        return None

async def _get_member(guild: discord.Guild, user_id: int) -> Optional[discord.Member]:
    m = guild.get_member(user_id)
    if m is not None:
        return m
    try:
        return await guild.fetch_member(user_id)
    except Exception:
        return None

async def reconcile_active_invitation(guild: discord.Guild):
    channel = discord.utils.get(guild.text_channels, name=ANNOUNCEMENT_CHANNEL_NAME)
    if channel is None:
        print(f"[startup] Channel #{ANNOUNCEMENT_CHANNEL_NAME} not found; skipping reconcile.")
        return

    active_message = None
    active_date = None

    async for msg in channel.history(limit=50):
        d = await _extract_date_from_message(msg)
        if d is not None:
            active_message = msg
            active_date = d
            break

    if not active_message or not active_date:
        print("[startup] No recent invite message found; skipping reconcile.")
        return

    yes_members = []
    maybe_members = []
    no_members = []

    for reaction in active_message.reactions:
        if str(reaction.emoji) == YES_EMOJI:
            async for user in reaction.users():
                if user.bot:
                    continue
                member = await _get_member(guild, user.id)
                if member and not member.bot:
                    yes_members.append(member)

        if str(reaction.emoji) == MAYBE_EMOJI:
            async for user in reaction.users():
                if user.bot:
                    continue
                member = await _get_member(guild, user.id)
                if member and not member.bot:
                    maybe_members.append(member)

        if str(reaction.emoji) == NO_EMOJI:
            async for user in reaction.users():
                if user.bot:
                    continue
                member = await _get_member(guild, user.id)
                if member and not member.bot:
                    no_members.append(member)

    # Priority: yes > maybe > unavailable (mutual exclusion)
    yes_ids = {m.id for m in yes_members}
    maybe_members = [m for m in maybe_members if m.id not in yes_ids]
    maybe_ids = {m.id for m in maybe_members}
    no_members = [m for m in no_members if m.id not in yes_ids and m.id not in maybe_ids]

    attendees = ", ".join(get_alias_for_member(m) for m in yes_members)
    possible = ", ".join(get_alias_for_member(m) for m in maybe_members)
    unavailable = ", ".join(get_alias_for_member(m) for m in no_members)

    update_schedule_entry(
        target_date=active_date,
        status="Scheduled",
        attendees=attendees,
        possible_attendees=possible,
        unavailable=unavailable,
    )

    print(f"[startup] Reconciled invite {active_date.strftime('%m/%d/%Y')} "
          f"(yes={len(yes_members)}, maybe={len(maybe_members)}).")

def register(bot):
    @bot.event
    async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
        if payload.user_id == bot.user.id:
            return

        emoji_str = str(payload.emoji)
        if emoji_str not in {YES_EMOJI, MAYBE_EMOJI, NO_EMOJI}:
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

        if emoji_str == YES_EMOJI:
            status = "yes"
            competing = {MAYBE_EMOJI, NO_EMOJI}
        elif emoji_str == MAYBE_EMOJI:
            status = "maybe"
            competing = {YES_EMOJI, NO_EMOJI}
        else:
            status = "unavailable"
            competing = {YES_EMOJI, MAYBE_EMOJI}

        update_schedule_attendance_for_member(target_date, member, status)

        for reaction in message.reactions:
            if str(reaction.emoji) in competing:
                async for user in reaction.users():
                    if user.id == member.id:
                        await message.remove_reaction(reaction.emoji, user)
                        break

    @bot.event
    async def on_raw_reaction_remove(payload: discord.RawReactionActionEvent):
        if payload.user_id == bot.user.id:
            return

        emoji_str = str(payload.emoji)
        if emoji_str not in {YES_EMOJI, MAYBE_EMOJI, NO_EMOJI}:
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

        has_yes = False
        has_maybe = False
        has_no = False

        for reaction in message.reactions:
            if str(reaction.emoji) in {YES_EMOJI, MAYBE_EMOJI, NO_EMOJI}:
                async for user in reaction.users():
                    if user.id == member.id:
                        if str(reaction.emoji) == YES_EMOJI:
                            has_yes = True
                        elif str(reaction.emoji) == MAYBE_EMOJI:
                            has_maybe = True
                        elif str(reaction.emoji) == NO_EMOJI:
                            has_no = True

        if has_yes:
            update_schedule_attendance_for_member(target_date, member, "yes")
        elif has_maybe:
            update_schedule_attendance_for_member(target_date, member, "maybe")
        elif has_no:
            update_schedule_attendance_for_member(target_date, member, "unavailable")
        else:
            update_schedule_attendance_for_member(target_date, member, "none")
