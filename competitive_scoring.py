"""
competitive_scoring.py
Discord commands for the new 2026 scoring system (SQLite-backed):

- /loggame
- /leaderboard
- /mystats and /stats
- /undo_last and /undo

Design choices (your preferences):
- Council-only logging/undo
- Players and winners are @mentions only (explicit roster)
- Minutes accept any integer, round to nearest 15
- Display points as integers; store to 2 decimals
- REVIEW flag if duration > 120 minutes
"""

import discord
from discord.ext import commands
from datetime import datetime, date as date_type
from typing import Optional

import scoring_config as cfg
from constants import COUNCIL_ROLE_NAME
from split_ids import split_id_for_date, current_split_id
import db
import scoring_engine as engine

# --- Permissions ---

def _has_council_role(member: discord.Member) -> bool:
    return any(r.name == COUNCIL_ROLE_NAME for r in getattr(member, "roles", []))

# --- Alias display helper (ID -> alias) ---
# Uses your guild cache; if user not found, fall back to ID string.
# If you later migrate aliases into SQLite, this is where you'd swap lookup logic.
def display_name_for_id(guild: discord.Guild, discord_id: int) -> str:
    m = guild.get_member(discord_id)
    if m:
        return m.display_name  # your scoreboard will use alias later if desired
    return f"User({discord_id})"

def parse_mentions_to_ids(members: list[discord.Member]) -> list[int]:
    # dedupe by id while keeping order
    seen = set()
    out = []
    for m in members:
        if m.bot:
            continue
        if m.id not in seen:
            out.append(m.id)
            seen.add(m.id)
    return out

def parse_date_override(date_str: Optional[str]) -> Optional[date_type]:
    """
    Optional override in MM/DD/YYYY.
    Returns date or None.
    """
    if not date_str or not date_str.strip():
        return None
    try:
        return datetime.strptime(date_str.strip(), "%m/%d/%Y").date()
    except ValueError:
        return None

def format_leaderboard_plain(guild: discord.Guild, split_id: str, ranked: list[dict], ineligible: list[dict], e0: float) -> str:
    top = ranked[:cfg.TOP_N_ELIGIBLE]
    lines = []
    lines.append(f"**Split:** {split_id}")
    lines.append(f"Eligibility: ≥ {cfg.MIN_ELIGIBLE_HOURS} hours AND ≥ {cfg.MIN_ELIGIBLE_NIGHTS} nights")
    lines.append(f"Baseline efficiency E0 (eligible avg): {e0:.2f} pts/hr")
    lines.append("")
    lines.append("**Eligible (ranked by AdjustedTotal):**")

    if not top:
        lines.append("_No eligible players yet._")
    else:
        for i, r in enumerate(top, start=1):
            name = display_name_for_id(guild, int(r["discord_id"]))
            adj = engine.display_points(r["adjusted_total"])
            pts = engine.display_points(r["total_points"])
            hrs = f"{r['hours']:.1f}"
            nights = str(r["nights"])
            lines.append(f"{i}. {name} — Adj:{adj} | Pts:{pts} | Hrs:{hrs} | Nights:{nights}")

    lines.append("")
    lines.append("**Ineligible:**")
    if not ineligible:
        lines.append("_None._")
    else:
        for r in ineligible:
            name = display_name_for_id(guild, int(r["discord_id"]))
            pts = engine.display_points(r["total_points"])
            hrs = f"{r['hours']:.1f}"
            nights = str(r["nights"])
            lines.append(f"- {name} — Pts:{pts} | Hrs:{hrs} | Nights:{nights} ({r['missing']})")

    return "\n".join(lines)

def build_leaderboard_embed(guild: discord.Guild, split_id: str, ranked: list[dict], ineligible: list[dict], e0: float) -> discord.Embed:
    embed = discord.Embed(
        title="Shrimp Pantheon — Split Leaderboard",
        description=f"**Split:** {split_id}\nEligibility: ≥ {cfg.MIN_ELIGIBLE_HOURS} hours AND ≥ {cfg.MIN_ELIGIBLE_NIGHTS} nights\n"
                    f"Baseline E0 (eligible avg): {e0:.2f} pts/hr",
    )

    top = ranked[:cfg.TOP_N_ELIGIBLE]
    if top:
        value_lines = []
        for i, r in enumerate(top, start=1):
            name = display_name_for_id(guild, int(r["discord_id"]))
            value_lines.append(
                f"**{i}. {name}** — Adj **{engine.display_points(r['adjusted_total'])}** | "
                f"Pts {engine.display_points(r['total_points'])} | "
                f"Hrs {r['hours']:.1f} | Nights {r['nights']}"
            )
        embed.add_field(name="Eligible (Top 5)", value="\n".join(value_lines), inline=False)
    else:
        embed.add_field(name="Eligible (Top 5)", value="_No eligible players yet._", inline=False)

    if ineligible:
        value_lines = []
        # keep this section short
        for r in ineligible[:10]:
            name = display_name_for_id(guild, int(r["discord_id"]))
            value_lines.append(
                f"• {name} — Pts {engine.display_points(r['total_points'])}, Hrs {r['hours']:.1f}, Nights {r['nights']} ({r['missing']})"
            )
        embed.add_field(name="Ineligible", value="\n".join(value_lines), inline=False)
    else:
        embed.add_field(name="Ineligible", value="_None._", inline=False)

    return embed

def register(bot: commands.Bot) -> None:
    # Ensure DB schema exists at import time
    db.init_db()

    @bot.tree.command(name="loggame", description="Log a game instance for the current split (Council only).")
    async def loggame(
        interaction: discord.Interaction,
        game: str,
        minutes: int,
        players: str,  # will be parsed from mentions in the interaction message content? (see below note)
        winners: str,
        date: Optional[str] = None,
        notes: Optional[str] = None,
    ):
        """
        NOTE (Discord limitation):
        Discord slash commands cannot accept a dynamic-length list of mentions in a single parameter cleanly
        unless you:
        - use a string field and parse <@id> patterns, OR
        - use multiple fixed "player1, player2, ..." params.

        For simplicity and to preserve your “@mentions only” preference,
        we’ll parse mentions from the raw string fields (players/winners) containing @mentions.

        Usage example:
        /loggame game:"Clue" minutes:47 players:"@A @B @C" winners:"@A" notes:"..."
        """
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        if not _has_council_role(interaction.user):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        # Parse date
        override_date = parse_date_override(date)
        if date and override_date is None:
            await interaction.response.send_message("Invalid date format. Use MM/DD/YYYY.", ephemeral=True)
            return

        local_d = override_date or datetime.now().date()
        split_id = split_id_for_date(local_d)

        # Round minutes
        rounded_minutes = engine.round_minutes_to_nearest_15(minutes)

        # Parse mentions out of the provided strings
        import re
        def extract_ids(s: str) -> list[int]:
            ids = re.findall(r"<@!?(\d+)>", s or "")
            out = []
            seen = set()
            for x in ids:
                v = int(x)
                if v not in seen:
                    out.append(v)
                    seen.add(v)
            return out

        player_ids = extract_ids(players)
        winner_ids = extract_ids(winners)

        err = engine.validate_game_instance(player_ids, winner_ids, rounded_minutes)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        comp = engine.compute_points(rounded_minutes, p=len(player_ids), w=len(winner_ids))

        # Insert record
        timestamp_utc = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        local_date_str = local_d.isoformat()
        notes_text = (notes or "").strip()

        game_id = db.insert_game_instance(
            timestamp_utc=timestamp_utc,
            local_date=local_date_str,
            split_id=split_id,
            game_name=game.strip(),
            duration_min=comp.duration_min,
            players=player_ids,
            winners=winner_ids,
            pool_points=comp.pool_points,
            points_per_winner=comp.points_per_winner,
            review_flag=comp.review_flag,
            notes=notes_text,
            logged_by=interaction.user.id,
            channel_id=interaction.channel_id,
            message_id=None,
        )

        # Build clean summary (no pings)
        guild = interaction.guild
        winner_names = ", ".join(display_name_for_id(guild, wid) for wid in winner_ids)
        all_names = ", ".join(display_name_for_id(guild, pid) for pid in player_ids)

        review_note = " **REVIEW** (duration > 2 hours)" if comp.review_flag else ""
        points_disp = engine.display_points(comp.points_per_winner)

        msg = (
            f"**Logged Game #{game_id}** — {game.strip()}\n"
            f"Date: {local_d.strftime('%m/%d/%Y')} | Duration: {comp.duration_min} min{review_note}\n"
            f"Players ({len(player_ids)}): {all_names}\n"
            f"Winner(s) ({len(winner_ids)}): {winner_names}\n"
            f"Points per winner: **{points_disp}** (pool {comp.pool_points:.2f})\n"
            f"Notes: {notes_text if notes_text else 'None'}"
        )

        await interaction.response.send_message(msg)

    @bot.tree.command(name="leaderboard", description="Show the current split leaderboard (adjusted shrinkage ranking).")
    async def leaderboard(interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        split_id = current_split_id()
        rows = db.fetch_game_instances_for_split(split_id)
        stats = engine.aggregate_split(rows)
        ranked, ineligible, e0 = engine.compute_leaderboard(stats)

        embed = build_leaderboard_embed(interaction.guild, split_id, ranked, ineligible, e0)
        plain = format_leaderboard_plain(interaction.guild, split_id, ranked, ineligible, e0)

        # Both: embed + fallback text
        await interaction.response.send_message(embed=embed)
        await interaction.followup.send(plain)

    @bot.tree.command(name="mystats", description="Show your split stats (points, hours, nights, efficiency).")
    async def mystats(interaction: discord.Interaction):
        await _stats_for_user(interaction, interaction.user.id)

    @bot.tree.command(name="stats", description="Show a player's split stats.")
    async def stats_cmd(interaction: discord.Interaction, player: discord.Member):
        await _stats_for_user(interaction, player.id)

    async def _stats_for_user(interaction: discord.Interaction, user_id: int):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        split_id = current_split_id()
        rows = db.fetch_game_instances_for_split(split_id)
        stats = engine.aggregate_split(rows)
        ranked, ineligible, e0 = engine.compute_leaderboard(stats)

        s = stats.get(user_id)
        if not s:
            await interaction.response.send_message("No games recorded for you in this split yet.", ephemeral=True)
            return

        # Find eligibility and adjusted metrics if eligible
        eligible_row = next((r for r in ranked if int(r["discord_id"]) == user_id), None)

        name = display_name_for_id(interaction.guild, user_id)
        pts = engine.display_points(s.total_points)
        hrs = f"{s.hours:.1f}"
        nights = str(s.nights_attended)
        raw_eff = f"{s.raw_efficiency:.2f}"

        eligible = (s.hours >= cfg.MIN_ELIGIBLE_HOURS and s.nights_attended >= cfg.MIN_ELIGIBLE_NIGHTS)
        if eligible and eligible_row:
            e_adj = f"{eligible_row['e_adj']:.2f}"
            adj_total = engine.display_points(eligible_row["adjusted_total"])
            status = "✅ Eligible"
        else:
            missing = []
            if s.hours < cfg.MIN_ELIGIBLE_HOURS:
                missing.append(f"{cfg.MIN_ELIGIBLE_HOURS - s.hours:.1f} more hours")
            if s.nights_attended < cfg.MIN_ELIGIBLE_NIGHTS:
                missing.append(f"{cfg.MIN_ELIGIBLE_NIGHTS - s.nights_attended} more nights")
            status = "❌ Ineligible (" + ", ".join(missing) + ")"
            e_adj = "N/A"
            adj_total = "N/A"

        msg = (
            f"**{name} — Stats ({split_id})**\n"
            f"Points: **{pts}** | Hours: **{hrs}** | Nights: **{nights}**\n"
            f"Raw efficiency: {raw_eff} pts/hr\n"
            f"Adjusted efficiency: {e_adj} pts/hr\n"
            f"AdjustedTotal: {adj_total}\n"
            f"Eligibility: {status}\n"
            f"(E0 baseline this split: {e0:.2f} pts/hr | H0: {cfg.H0_HOURS}h)"
        )

        await interaction.response.send_message(msg, ephemeral=True)

    @bot.tree.command(name="undo_last", description="Undo the most recently logged game (Council only).")
    async def undo_last(interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        if not _has_council_role(interaction.user):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        split_id = current_split_id()
        last_id = db.get_last_game_instance_id(split_id=split_id)
        if last_id is None:
            await interaction.response.send_message("No games exist to undo in the current split.", ephemeral=True)
            return

        ok = db.delete_game_instance(last_id)
        await interaction.response.send_message(
            f"{'✅' if ok else '❌'} Undo last: game #{last_id} {'deleted' if ok else 'not found'}.",
            ephemeral=True
        )

    @bot.tree.command(name="undo", description="Undo a specific game_id (Council only).")
    async def undo(interaction: discord.Interaction, game_id: int):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        if not _has_council_role(interaction.user):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        ok = db.delete_game_instance(int(game_id))
        await interaction.response.send_message(
            f"{'✅' if ok else '❌'} Undo: game #{game_id} {'deleted' if ok else 'not found'}.",
            ephemeral=True
        )
