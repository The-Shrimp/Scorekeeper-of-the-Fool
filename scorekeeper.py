"""
scorekeeper.py
Rotation-based scorekeeper assignment system.

- ensure_rotation_seeded   called on bot startup; only seeds once
- assign_scorekeeper       called by /schedulegamenight after posting embed
- handle_dm_reaction_add   called from rsvp.py for DM ❌ reactions (decline)
- register(bot)            registers /scorekeeper_rotation slash command
"""

import random
from datetime import datetime
from typing import Optional

import discord

import db
from public_identity import display_name_for_discord
from constants import COUNCIL_ROLE_NAME, COUNCIL_CHANNEL_NAME

_DECLINE_EMOJI = "\u274c"  # ❌  (used only in DMs for scorekeeper decline)

_DM_TEMPLATE = (
    "\U0001f3b2 You're the scorekeeper for Game Night on **{date}**!\n\n"
    "This means you're responsible for logging games in the bot using `/loggame` during game night.\n\n"
    "If you're unable to fulfil this role, react \u274c to this message and "
    "it will be passed to the next council member.\n\n"
    "Non-response is treated as acceptance. See you Saturday!"
)


def _fmt_date(date_iso: str) -> str:
    try:
        dt = datetime.strptime(date_iso, "%Y-%m-%d")
        return f"{dt.strftime('%B')} {dt.day}, {dt.year}"
    except Exception:
        return date_iso


def _has_council(member: discord.Member) -> bool:
    return any(r.name == COUNCIL_ROLE_NAME for r in getattr(member, "roles", []))


async def ensure_rotation_seeded(guild: discord.Guild) -> None:
    """Seed the scorekeeper rotation from council members if the table is empty."""
    if db.get_rotation():
        return
    council_role = discord.utils.get(guild.roles, name=COUNCIL_ROLE_NAME)
    if council_role is None:
        print(f"[scorekeeper] Role '{COUNCIL_ROLE_NAME}' not found; skipping rotation seed.")
        return
    members = [m for m in council_role.members if not m.bot]
    random.shuffle(members)
    for i, member in enumerate(members, start=1):
        db.upsert_rotation(str(member.id), i)
    print(f"[scorekeeper] Seeded rotation with {len(members)} council members.")


async def assign_scorekeeper(
    bot: discord.Client,
    guild: discord.Guild,
    date_iso: str,
) -> Optional[discord.Member]:
    """Pick the next available council member, assign them, and send a DM."""
    rotation = db.get_rotation()
    declined = db.get_declined_for_night(date_iso)

    for entry in rotation:
        did = str(entry["discord_id"])
        if did in declined:
            continue
        if db.is_excluded(did):
            continue
        try:
            member = guild.get_member(int(did)) or await guild.fetch_member(int(did))
        except Exception:
            continue
        if member is None or member.bot:
            continue

        db.upsert_assignment(date_iso, did, "pending")
        db.set_scorekeeper_for_night(date_iso, did)
        await _send_assignment_dm(member, date_iso)
        return member

    await _warn_no_scorekeeper(bot, guild, date_iso)
    return None


async def _send_assignment_dm(member: discord.Member, date_iso: str) -> None:
    try:
        msg = await member.send(_DM_TEMPLATE.format(date=_fmt_date(date_iso)))
        await msg.add_reaction(_DECLINE_EMOJI)
    except Exception as exc:
        print(f"[scorekeeper] DM failed for {member.id} ({date_iso}): {exc}")


async def _warn_no_scorekeeper(bot: discord.Client, guild: discord.Guild, date_iso: str) -> None:
    channel = discord.utils.get(guild.text_channels, name=COUNCIL_CHANNEL_NAME)
    if channel is None:
        print(f"[scorekeeper] Council channel '{COUNCIL_CHANNEL_NAME}' not found.")
        return
    try:
        await channel.send(
            f"No scorekeeper could be assigned for Game Night on **{_fmt_date(date_iso)}**. "
            "All council members have declined or are unavailable. Please coordinate manually."
        )
    except Exception as exc:
        print(f"[scorekeeper] Failed to post council warning: {exc}")


async def handle_dm_reaction_add(bot: discord.Client, payload: discord.RawReactionActionEvent) -> None:
    """
    Called from rsvp.py when a reaction fires in a DM.
    Handles ❌ declines from council members assigned as scorekeeper.
    """
    if str(payload.emoji) != _DECLINE_EMOJI:
        return

    assignment = db.get_pending_assignment_for_user(str(payload.user_id))
    if assignment is None:
        return

    date_iso = assignment["date_iso"]
    declined_id = str(payload.user_id)

    db.upsert_assignment(date_iso, declined_id, "declined")

    # Find next candidate in rotation
    rotation = db.get_rotation()
    declined_set = db.get_declined_for_night(date_iso)

    new_member: Optional[discord.Member] = None
    new_id: Optional[str] = None
    for entry in rotation:
        did = str(entry["discord_id"])
        if did == declined_id or did in declined_set:
            continue
        if db.is_excluded(did):
            continue
        for guild in bot.guilds:
            candidate = guild.get_member(int(did))
            if candidate and not candidate.bot:
                new_member = candidate
                new_id = did
                break
        if new_member:
            break

    # Confirm to the decliner via DM
    try:
        user = await bot.fetch_user(payload.user_id)
        dm = await bot.create_dm(user)
        if new_member:
            await dm.send(
                f"Understood \u2014 passing scorekeeper duty to **{display_name_for_discord(new_member.id, db.get_alias(new_member.id))}** "
                f"for {_fmt_date(date_iso)}."
            )
        else:
            await dm.send(
                f"Understood. Unfortunately all council members have declined for {_fmt_date(date_iso)}. "
                "The council has been notified."
            )
    except Exception:
        pass

    if new_member is None or new_id is None:
        db.set_scorekeeper_for_night(date_iso, None)
        from announcements import queue_refresh
        for guild in bot.guilds:
            await _warn_no_scorekeeper(bot, guild, date_iso)
            await queue_refresh(bot, date_iso)
        return

    # Swap positions so stand-in moves later, decliner moves sooner
    db.swap_rotation_positions(declined_id, new_id)
    db.upsert_assignment(date_iso, new_id, "pending")
    db.set_scorekeeper_for_night(date_iso, new_id)
    await _send_assignment_dm(new_member, date_iso)

    from announcements import queue_refresh
    await queue_refresh(bot, date_iso)


def register(bot) -> None:
    @bot.tree.command(
        name="scorekeeper_rotation",
        description="Show the current scorekeeper rotation order (Council only).",
    )
    async def scorekeeper_rotation_cmd(interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("Server only.", ephemeral=True)
            return
        if not _has_council(interaction.user):
            await interaction.response.send_message("You do not have permission.", ephemeral=True)
            return

        rotation = db.get_rotation()
        if not rotation:
            await interaction.response.send_message("Rotation is empty.", ephemeral=True)
            return

        alias_map = db.load_alias_map()
        exclusions = {e["discord_id"]: e for e in db.get_rotation_exclusions()}
        lines = []
        for entry in rotation:
            did = str(entry["discord_id"])
            name = display_name_for_discord(int(did), alias_map.get(did))
            if did in exclusions:
                ex = exclusions[did]
                suffix = f" [excluded until {ex['excluded_until']}]" if ex["excluded_until"] else " [excluded]"
                name += suffix
            lines.append(f"{entry['position']}. {name}")

        await interaction.response.send_message(
            "**Scorekeeper Rotation**\n" + "\n".join(lines),
            ephemeral=True,
        )

    @bot.tree.command(
        name="exclude_scorekeeper",
        description="Exclude a council member from the scorekeeper rotation (Council only).",
    )
    @discord.app_commands.describe(
        member="The council member to exclude.",
        until="Exclude until this date (YYYY-MM-DD). Omit for permanent exclusion.",
        reason="Optional reason.",
    )
    async def exclude_scorekeeper_cmd(
        interaction: discord.Interaction,
        member: discord.Member,
        until: Optional[str] = None,
        reason: Optional[str] = None,
    ):
        if not interaction.guild:
            await interaction.response.send_message("Server only.", ephemeral=True)
            return
        if not _has_council(interaction.user):
            await interaction.response.send_message("You do not have permission.", ephemeral=True)
            return

        if until:
            try:
                datetime.strptime(until, "%Y-%m-%d")
            except ValueError:
                await interaction.response.send_message(
                    "Invalid date format. Use YYYY-MM-DD.", ephemeral=True
                )
                return

        db.add_rotation_exclusion(str(member.id), until, reason)

        name = display_name_for_discord(member.id, db.get_alias(member.id))
        msg = f"**{name}** excluded from scorekeeper rotation"
        msg += f" until {until}." if until else " permanently."
        if reason:
            msg += f" Reason: {reason}"
        await interaction.response.send_message(msg, ephemeral=True)

    @bot.tree.command(
        name="unexclude_scorekeeper",
        description="Restore a council member to the scorekeeper rotation (Council only).",
    )
    @discord.app_commands.describe(member="The council member to restore.")
    async def unexclude_scorekeeper_cmd(
        interaction: discord.Interaction,
        member: discord.Member,
    ):
        if not interaction.guild:
            await interaction.response.send_message("Server only.", ephemeral=True)
            return
        if not _has_council(interaction.user):
            await interaction.response.send_message("You do not have permission.", ephemeral=True)
            return

        db.remove_rotation_exclusion(str(member.id))
        name = display_name_for_discord(member.id, db.get_alias(member.id))
        await interaction.response.send_message(
            f"**{name}** restored to scorekeeper rotation.", ephemeral=True
        )
