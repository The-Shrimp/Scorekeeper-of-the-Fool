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
import json

import scoring_config as cfg
from constants import COUNCIL_ROLE_NAME
from split_ids import split_id_for_date, current_split_id
import db
import scoring_engine as engine
import aliases

# --- Permissions ---

def _has_council_role(member: discord.Member) -> bool:
    return any(r.name == COUNCIL_ROLE_NAME for r in getattr(member, "roles", []))

# --- Alias display helper (ID -> alias) ---
# Uses your guild cache; if user not found, fall back to ID string.
# If you later migrate aliases into SQLite, this is where you'd swap lookup logic.
def display_name_for_id(guild: discord.Guild, discord_id: int) -> str:
    alias = db.get_alias(discord_id)
    if alias:
        return alias
    m = guild.get_member(discord_id)
    if m:
        return m.display_name
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


def resolve_roster_ids(guild: discord.Guild, raw_value: str) -> tuple[list[int], list[str]]:
    """
    Resolve a roster field using @mentions and alias/display-name lookup.
    Non-mention names should be comma-separated so multi-word aliases still work.
    """
    members, unresolved = aliases.resolve_aliases_to_members(guild, raw_value)
    return parse_mentions_to_ids(members), unresolved


def ids_from_json(raw_json: str) -> list[int]:
    return [int(v) for v in json.loads(raw_json or "[]")]


def format_lineage(row: dict) -> str:
    parts = []
    if row.get("supersedes_game_id") is not None:
        parts.append(f"supersedes #{row['supersedes_game_id']}")
    if row.get("superseded_by_game_id") is not None:
        parts.append(f"superseded by #{row['superseded_by_game_id']}")
    return ", ".join(parts) if parts else "Original record"


def format_audit_entry(entry: dict) -> str:
    parts = [f"#{entry['id']}", str(entry["action"]), str(entry["timestamp_utc"])]
    if entry.get("actor_discord_id"):
        parts.append(f"by {entry['actor_discord_id']}")

    payload = None
    if entry.get("payload_json"):
        try:
            payload = json.loads(str(entry["payload_json"]))
        except Exception:
            payload = None

    if isinstance(payload, dict):
        if payload.get("original_game_id") is not None and payload.get("replacement_game_id") is not None:
            parts.append(
                f"orig #{payload['original_game_id']} -> repl #{payload['replacement_game_id']}"
            )

    return " | ".join(parts)

def _trunc(s: str, n: int) -> str:
    """Truncate to n chars with ellipsis."""
    return s[:n - 1] + "…" if len(s) > n else s

def _code(text: str) -> str:
    """Wrap in a monospace Discord code block."""
    return f"```\n{text}\n```"

_GOLD   = discord.Color.from_rgb(255, 215, 0)
_GREEN  = discord.Color.green()
_ORANGE = discord.Color.orange()
_TEAL   = discord.Color.teal()


def build_leaderboard_embed(
    guild: discord.Guild,
    split_id: str,
    ranked: list[dict],
    ineligible: list[dict],
    e0: float,
) -> discord.Embed:
    embed = discord.Embed(title=f"🏆 Leaderboard — {split_id}", color=_GOLD)
    embed.set_footer(
        text=f"Eligibility: ≥{cfg.MIN_ELIGIBLE_HOURS}h & ≥{cfg.MIN_ELIGIBLE_NIGHTS} nights  |  "
             f"E0: {e0:.2f} pts/hr  |  H0: {cfg.H0_HOURS}h"
    )

    top = ranked[:cfg.TOP_N_ELIGIBLE]
    if top:
        names = [display_name_for_id(guild, int(r["discord_id"])) for r in top]
        nw = max(min(max(len(n) for n in names), 16), 4)
        header = f"{'#':<3} {'Name':<{nw}}  {'Adj':>5}  {'Pts':>5}  {'Hrs':>5}  {'Nts':>3}"
        sep    = "─" * len(header)
        rows   = []
        for i, (r, raw_name) in enumerate(zip(top, names), 1):
            rows.append(
                f"{i:<3} {_trunc(raw_name, nw):<{nw}}  "
                f"{engine.display_points(r['adjusted_total']):>5}  "
                f"{engine.display_points(r['total_points']):>5}  "
                f"{r['hours']:>5.1f}  "
                f"{r['nights']:>3}"
            )
        embed.add_field(
            name=f"Eligible Players (Top {cfg.TOP_N_ELIGIBLE})",
            value=_code(f"{header}\n{sep}\n" + "\n".join(rows)),
            inline=False,
        )
    else:
        embed.add_field(
            name=f"Eligible Players (Top {cfg.TOP_N_ELIGIBLE})",
            value="_No eligible players yet._",
            inline=False,
        )

    if ineligible:
        names_i = [display_name_for_id(guild, int(r["discord_id"])) for r in ineligible[:10]]
        nw_i = max(min(max(len(n) for n in names_i), 14), 4)
        rows_i = []
        for r, raw_name in zip(ineligible[:10], names_i):
            rows_i.append(
                f"{_trunc(raw_name, nw_i):<{nw_i}}  "
                f"{engine.display_points(r['total_points']):>5}  "
                f"{r['hours']:>4.1f}h  "
                f"{r['nights']:>2} nts  "
                f"{r['missing']}"
            )
        embed.add_field(name="Ineligible", value=_code("\n".join(rows_i)), inline=False)
    else:
        embed.add_field(name="Ineligible", value="_None._", inline=False)

    return embed


def build_stats_embed(
    name: str,
    split_id: str,
    s,
    detail: dict,
    eligible_row: Optional[dict],
    e0: float,
    eligible: bool,
) -> discord.Embed:
    color = _GREEN if eligible else _ORANGE
    embed = discord.Embed(title=f"{name} — {split_id}", color=color)

    pts_per_night = (
        engine.display_points(s.total_points / s.nights_attended)
        if s.nights_attended > 0 else "0"
    )

    embed.add_field(name="Points",  value=engine.display_points(s.total_points), inline=True)
    embed.add_field(name="Hours",   value=f"{s.hours:.1f}",                      inline=True)
    embed.add_field(name="Nights",  value=str(s.nights_attended),                 inline=True)

    embed.add_field(name="Games",    value=str(detail["games_played"]),            inline=True)
    embed.add_field(name="Wins",     value=str(detail["wins"]),                    inline=True)
    embed.add_field(name="Win Rate", value=f"{detail['win_rate'] * 100:.0f}%",     inline=True)

    embed.add_field(name="Pts / Night",  value=pts_per_night,             inline=True)
    embed.add_field(name="Most Played",  value=detail["most_played_game"], inline=True)
    embed.add_field(name="\u200b",       value="\u200b",                   inline=True)

    embed.add_field(name="Raw Eff",
                    value=f"{s.raw_efficiency:.2f} pts/hr",
                    inline=True)
    embed.add_field(name="Adj Eff",
                    value=f"{eligible_row['e_adj']:.2f} pts/hr" if eligible_row else "N/A",
                    inline=True)
    embed.add_field(name="Adj Total",
                    value=engine.display_points(eligible_row["adjusted_total"]) if eligible_row else "N/A",
                    inline=True)

    if eligible:
        status_str = "✅ Eligible"
    else:
        missing = []
        if s.hours < cfg.MIN_ELIGIBLE_HOURS:
            missing.append(f"{cfg.MIN_ELIGIBLE_HOURS - s.hours:.1f} more hours")
        if s.nights_attended < cfg.MIN_ELIGIBLE_NIGHTS:
            missing.append(f"{cfg.MIN_ELIGIBLE_NIGHTS - s.nights_attended} more nights")
        status_str = "❌ Ineligible — needs " + " & ".join(missing)

    embed.add_field(name="Eligibility", value=status_str, inline=False)
    embed.set_footer(text=f"E0: {e0:.2f} pts/hr  |  H0: {cfg.H0_HOURS}h")
    return embed


def build_splitstats_embed(split_id: str, summary: dict) -> discord.Embed:
    embed = discord.Embed(title=f"📊 Split Summary — {split_id}", color=_TEAL)
    embed.add_field(name="Games Logged",   value=str(summary["total_games"]),     inline=True)
    embed.add_field(name="Unique Players", value=str(summary["total_players"]),   inline=True)
    embed.add_field(name="Total Hours",    value=f"{summary['total_hours']:.1f}", inline=True)
    embed.add_field(
        name="Most Played (by games)",
        value=f"{summary['most_played_game']} ({summary['most_played_count']}×)",
        inline=True,
    )
    embed.add_field(
        name="Most Played (by time)",
        value=f"{summary['most_time_game']} ({summary['most_time_hours']:.1f}h)",
        inline=True,
    )
    embed.add_field(
        name="Busiest Night",
        value=f"{summary['busiest_night']} ({summary['busiest_night_games']} games)",
        inline=True,
    )
    return embed


def build_loggame_embed(
    game_id: int,
    game_name: str,
    raw_game_name: str,
    local_d,
    comp,
    player_ids: list[int],
    winner_ids: list[int],
    guild: discord.Guild,
    notes_text: str,
) -> discord.Embed:
    color = _ORANGE if comp.review_flag else _TEAL
    normalized = game_name != raw_game_name

    description = f"**{game_name}**"
    if normalized:
        description += f"\n*normalized from \"{raw_game_name}\"*"

    embed = discord.Embed(
        title=f"✅ Game #{game_id} Logged",
        description=description,
        color=color,
    )

    duration_val = f"{comp.duration_min} min"
    if comp.review_flag:
        duration_val += "  ⚠️ REVIEW"

    embed.add_field(name="Date",     value=local_d.strftime("%m/%d/%Y"), inline=True)
    embed.add_field(name="Duration", value=duration_val,                  inline=True)
    embed.add_field(name="\u200b",   value="\u200b",                      inline=True)

    all_names    = ", ".join(display_name_for_id(guild, pid) for pid in player_ids)
    winner_names = ", ".join(display_name_for_id(guild, wid) for wid in winner_ids)
    embed.add_field(name=f"Players ({len(player_ids)})",   value=all_names or "—",    inline=False)
    embed.add_field(name=f"Winner(s) ({len(winner_ids)})", value=winner_names or "—", inline=False)

    embed.add_field(name="Pts / Winner",
                    value=f"**{engine.display_points(comp.points_per_winner)}**",
                    inline=True)
    embed.add_field(name="Pool Points",
                    value=f"{comp.pool_points:.2f}",
                    inline=True)

    if notes_text:
        embed.add_field(name="Notes", value=notes_text, inline=False)

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

        This implementation accepts:
        - @mentions, or
        - comma-separated aliases / display names / usernames.

        Usage example:
        /loggame game:"Clue" minutes:47 players:"@A @B, Shrimp" winners:"@A" notes:"..."
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

        player_ids, unresolved_players = resolve_roster_ids(interaction.guild, players)
        if unresolved_players:
            unresolved_text = ", ".join(unresolved_players)
            await interaction.response.send_message(
                f"Could not resolve player(s): {unresolved_text}. Use @mentions or comma-separated aliases/display names.",
                ephemeral=True,
            )
            return

        winner_ids, unresolved_winners = resolve_roster_ids(interaction.guild, winners)
        if unresolved_winners:
            unresolved_text = ", ".join(unresolved_winners)
            await interaction.response.send_message(
                f"Could not resolve winner(s): {unresolved_text}. Use @mentions or comma-separated aliases/display names.",
                ephemeral=True,
            )
            return

        err = engine.validate_game_instance(player_ids, winner_ids, rounded_minutes)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        comp = engine.compute_points(rounded_minutes, p=len(player_ids), w=len(winner_ids))

        # Insert record
        timestamp_utc = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        local_date_str = local_d.isoformat()
        notes_text = (notes or "").strip()

        raw_game_name = game.strip()
        normalized_game_name = db.normalize_game_name(raw_game_name)

        game_id = db.insert_game_instance(
            timestamp_utc=timestamp_utc,
            local_date=local_date_str,
            split_id=split_id,
            game_name=normalized_game_name,
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

        db.write_audit(
            action="INSERT_GAME",
            actor_id=str(interaction.user.id),
            target_id=str(game_id),
            payload={"game_name": normalized_game_name, "split_id": split_id, "duration_min": comp.duration_min,
                     "players": player_ids, "winners": winner_ids},
        )

        # Recompute derived attendance for the night from active game logs
        db.recompute_night_attendance(local_date_str)

        embed = build_loggame_embed(
            game_id=game_id,
            game_name=normalized_game_name,
            raw_game_name=raw_game_name,
            local_d=local_d,
            comp=comp,
            player_ids=player_ids,
            winner_ids=winner_ids,
            guild=interaction.guild,
            notes_text=notes_text,
        )
        await interaction.response.send_message(embed=embed)

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
        await interaction.response.send_message(embed=embed)

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

        detail = engine.compute_player_detail(user_id, rows)
        name = display_name_for_id(interaction.guild, user_id)
        eligible = (s.hours >= cfg.MIN_ELIGIBLE_HOURS and s.nights_attended >= cfg.MIN_ELIGIBLE_NIGHTS)

        embed = build_stats_embed(name, split_id, s, detail, eligible_row, e0, eligible)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="splitstats", description="Show aggregate stats for the current split.")
    async def splitstats(interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return

        split_id = current_split_id()
        rows = db.fetch_game_instances_for_split(split_id)

        if not rows:
            await interaction.response.send_message(f"No games logged yet for split {split_id}.", ephemeral=True)
            return

        summary = engine.compute_split_summary(rows)
        embed = build_splitstats_embed(split_id, summary)
        await interaction.response.send_message(embed=embed)

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

        moved = db.move_game_to_review(last_id, str(interaction.user.id))
        if moved:
            db.recompute_night_attendance(str(moved["local_date"]))
            db.write_audit(action="SOFT_DELETE_GAME", actor_id=str(interaction.user.id),
                           target_id=str(last_id), payload=moved)
            await interaction.response.send_message(
                f"✅ Game #{last_id} moved to review (not permanently deleted). Use `/recover {last_id}` to restore it.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(f"❌ Game #{last_id} not found.", ephemeral=True)

    @bot.tree.command(name="undo", description="Undo a specific game_id (Council only).")
    async def undo(interaction: discord.Interaction, game_id: int):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        if not _has_council_role(interaction.user):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        moved = db.move_game_to_review(int(game_id), str(interaction.user.id))
        if moved:
            db.recompute_night_attendance(str(moved["local_date"]))
            db.write_audit(action="SOFT_DELETE_GAME", actor_id=str(interaction.user.id),
                           target_id=str(game_id), payload=moved)
            await interaction.response.send_message(
                f"✅ Game #{game_id} moved to review (not permanently deleted). Use `/recover {game_id}` to restore it.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(f"❌ Game #{game_id} not found.", ephemeral=True)

    @bot.tree.command(name="recover", description="Restore a game from review back to the leaderboard (Council only).")
    async def recover(interaction: discord.Interaction, game_id: int):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        if not _has_council_role(interaction.user):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        restored = db.restore_game_from_review(int(game_id))
        if restored:
            db.recompute_night_attendance(str(restored["local_date"]))
            db.write_audit(action="RECOVER_GAME", actor_id=str(interaction.user.id),
                           target_id=str(game_id), payload=restored)
            await interaction.response.send_message(
                f"✅ Game #{game_id} ({restored['game_name']}, {restored['local_date']}) restored to the leaderboard.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"❌ Game #{game_id} not found in review. It may have already been restored or never moved.",
                ephemeral=True,
            )

    @bot.tree.command(name="editgame", description="Create a corrected replacement for an existing game (Council only).")
    async def editgame(
        interaction: discord.Interaction,
        game_id: int,
        game: Optional[str] = None,
        minutes: Optional[int] = None,
        players: Optional[str] = None,
        winners: Optional[str] = None,
        date: Optional[str] = None,
        notes: Optional[str] = None,
    ):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        if not _has_council_role(interaction.user):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        original = db.fetch_game_instance_by_id(int(game_id))
        if not original:
            await interaction.response.send_message(f"❌ Game #{game_id} not found.", ephemeral=True)
            return
        if original.get("superseded_by_game_id") is not None:
            await interaction.response.send_message(
                f"❌ Game #{game_id} has already been superseded by game #{original['superseded_by_game_id']}.",
                ephemeral=True,
            )
            return

        override_date = parse_date_override(date)
        if date and override_date is None:
            await interaction.response.send_message("Invalid date format. Use MM/DD/YYYY.", ephemeral=True)
            return

        if players is not None:
            player_ids, unresolved_players = resolve_roster_ids(interaction.guild, players)
            if unresolved_players:
                unresolved_text = ", ".join(unresolved_players)
                await interaction.response.send_message(
                    f"Could not resolve player(s): {unresolved_text}. Use @mentions or comma-separated aliases/display names.",
                    ephemeral=True,
                )
                return
        else:
            player_ids = ids_from_json(str(original["players_json"]))

        if winners is not None:
            winner_ids, unresolved_winners = resolve_roster_ids(interaction.guild, winners)
            if unresolved_winners:
                unresolved_text = ", ".join(unresolved_winners)
                await interaction.response.send_message(
                    f"Could not resolve winner(s): {unresolved_text}. Use @mentions or comma-separated aliases/display names.",
                    ephemeral=True,
                )
                return
        else:
            winner_ids = ids_from_json(str(original["winners_json"]))

        base_minutes = int(original["duration_min"])
        rounded_minutes = base_minutes if minutes is None else engine.round_minutes_to_nearest_15(minutes)

        err = engine.validate_game_instance(player_ids, winner_ids, rounded_minutes)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return

        if override_date is not None:
            local_d = override_date
        else:
            local_d = datetime.strptime(str(original["local_date"]), "%Y-%m-%d").date()
        split_id = split_id_for_date(local_d)

        raw_game_name = (game.strip() if game is not None else str(original["game_name"]))
        normalized_game_name = db.normalize_game_name(raw_game_name)
        notes_text = str(original["notes"] or "") if notes is None else notes.strip()

        comp = engine.compute_points(rounded_minutes, p=len(player_ids), w=len(winner_ids))
        replacement_id = db.create_replacement_game(
            original_game_id=int(game_id),
            timestamp_utc=datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
            local_date=local_d.isoformat(),
            split_id=split_id,
            game_name=normalized_game_name,
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
        if replacement_id is None:
            await interaction.response.send_message(
                f"❌ Game #{game_id} could not be superseded. No replacement was linked.",
                ephemeral=True,
            )
            return

        old_date = str(original["local_date"])
        new_date = local_d.isoformat()
        db.recompute_night_attendance(old_date)
        if new_date != old_date:
            db.recompute_night_attendance(new_date)

        db.write_audit(
            action="EDIT_GAME",
            actor_id=str(interaction.user.id),
            target_id=str(replacement_id),
            payload={
                "original_game_id": int(game_id),
                "replacement_game_id": replacement_id,
                "game_name": normalized_game_name,
                "split_id": split_id,
                "duration_min": comp.duration_min,
                "players": player_ids,
                "winners": winner_ids,
                "local_date": new_date,
            },
        )

        await interaction.response.send_message(
            f"✅ Game #{game_id} superseded by game #{replacement_id}. The replacement is now the active record.",
            ephemeral=True,
        )

    @bot.tree.command(name="addgame", description="Register a canonical game name for normalization (Council only).")
    async def addgame(interaction: discord.Interaction, name: str):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        if not _has_council_role(interaction.user):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        inserted = db.add_canonical_game(name.strip(), str(interaction.user.id))
        if inserted:
            db.write_audit(action="ADD_CANONICAL_GAME", actor_id=str(interaction.user.id),
                           target_id=name.strip(), payload={"canonical_name": name.strip()})
            await interaction.response.send_message(
                f"✅ **{name.strip()}** registered as a canonical game name.", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ A canonical game named **{name.strip()}** already exists.", ephemeral=True
            )

    @bot.tree.command(name="mapgamealias", description="Map a raw game name to a canonical game name (Council only).")
    async def mapgamealias(interaction: discord.Interaction, raw_name: str, canonical_name: str):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        if not _has_council_role(interaction.user):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        error = db.map_game_alias(raw_name.strip(), canonical_name.strip(), str(interaction.user.id))
        if error:
            await interaction.response.send_message(f"❌ {error}", ephemeral=True)
            return

        db.write_audit(
            action="MAP_GAME_ALIAS",
            actor_id=str(interaction.user.id),
            target_id=raw_name.strip(),
            payload={"raw_name": raw_name.strip(), "canonical_name": canonical_name.strip()},
        )
        await interaction.response.send_message(
            f"✅ Mapped **{raw_name.strip()}** to **{canonical_name.strip()}**.",
            ephemeral=True,
        )

    @bot.tree.command(name="reviewqueue", description="List recently soft-deleted games waiting in review (Council only).")
    async def reviewqueue(interaction: discord.Interaction, limit: Optional[int] = 10):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        if not _has_council_role(interaction.user):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        rows = db.fetch_review_games(limit=limit or 10)
        if not rows:
            await interaction.response.send_message("No games are currently in review.", ephemeral=True)
            return

        lines = []
        for row in rows:
            lines.append(
                f"#{row['id']}  {row['local_date']}  {row['game_name']}  deleted by {row['deleted_by']}"
            )

        await interaction.response.send_message(
            _code("\n".join(lines)),
            ephemeral=True,
        )

    @bot.tree.command(name="reviewgame", description="Show the details of a soft-deleted game (Council only).")
    async def reviewgame(interaction: discord.Interaction, game_id: int):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        if not _has_council_role(interaction.user):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        row = db.fetch_review_game(int(game_id))
        if not row:
            await interaction.response.send_message(f"❌ Game #{game_id} is not in review.", ephemeral=True)
            return

        player_names = ", ".join(
            display_name_for_id(interaction.guild, int(pid))
            for pid in json.loads(row["players_json"])
        ) or "—"
        winner_names = ", ".join(
            display_name_for_id(interaction.guild, int(pid))
            for pid in json.loads(row["winners_json"])
        ) or "—"

        embed = discord.Embed(
            title=f"Review Game #{row['id']}",
            description=f"**{row['game_name']}** on {row['local_date']}",
            color=_ORANGE,
        )
        embed.add_field(name="Players", value=player_names, inline=False)
        embed.add_field(name="Winners", value=winner_names, inline=False)
        embed.add_field(name="Duration", value=f"{row['duration_min']} min", inline=True)
        embed.add_field(name="Pts / Winner", value=engine.display_points(float(row["points_per_winner"])), inline=True)
        embed.add_field(name="Lineage", value=format_lineage(row), inline=False)
        embed.add_field(name="Deleted", value=f"{row['deleted_at_utc']} by {row['deleted_by']}", inline=False)
        if row.get("notes"):
            embed.add_field(name="Notes", value=str(row["notes"]), inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="gameinfo", description="Show the details and correction lineage for an active game (Council only).")
    async def gameinfo(interaction: discord.Interaction, game_id: int):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        if not _has_council_role(interaction.user):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        row = db.fetch_game_instance_by_id(int(game_id))
        if not row:
            review_row = db.fetch_review_game(int(game_id))
            if review_row:
                await interaction.response.send_message(
                    f"Game #{game_id} is currently in review. Use `/reviewgame {game_id}`.",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(f"❌ Game #{game_id} not found.", ephemeral=True)
            return

        player_names = ", ".join(
            display_name_for_id(interaction.guild, int(pid))
            for pid in ids_from_json(str(row["players_json"]))
        ) or "—"
        winner_names = ", ".join(
            display_name_for_id(interaction.guild, int(pid))
            for pid in ids_from_json(str(row["winners_json"]))
        ) or "—"

        embed = discord.Embed(
            title=f"Game #{row['id']}",
            description=f"**{row['game_name']}** on {row['local_date']}",
            color=_TEAL if row.get("superseded_by_game_id") is None else _ORANGE,
        )
        embed.add_field(name="Players", value=player_names, inline=False)
        embed.add_field(name="Winners", value=winner_names, inline=False)
        embed.add_field(name="Duration", value=f"{row['duration_min']} min", inline=True)
        embed.add_field(name="Pts / Winner", value=engine.display_points(float(row["points_per_winner"])), inline=True)
        embed.add_field(name="Pool Points", value=f"{float(row['pool_points']):.2f}", inline=True)
        embed.add_field(name="Lineage", value=format_lineage(row), inline=False)
        embed.add_field(
            name="Status",
            value="Active leaderboard record" if row.get("superseded_by_game_id") is None else "Superseded historical record",
            inline=False,
        )
        if row.get("notes"):
            embed.add_field(name="Notes", value=str(row["notes"]), inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @bot.tree.command(name="auditgame", description="Show recent audit entries related to a game (Council only).")
    async def auditgame(interaction: discord.Interaction, game_id: int, limit: Optional[int] = 10):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        if not _has_council_role(interaction.user):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        rows = db.fetch_audit_for_game(int(game_id), limit=limit or 10)
        if not rows:
            await interaction.response.send_message(
                f"No recent audit entries found for game #{game_id}.",
                ephemeral=True,
            )
            return

        text = "\n".join(format_audit_entry(row) for row in rows)
        await interaction.response.send_message(_code(text), ephemeral=True)

    @bot.tree.command(name="renamegame", description="Rename a canonical game and update all historical records (Council only).")
    async def renamegame(interaction: discord.Interaction, old_name: str, new_name: str):
        if not interaction.guild:
            await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
            return
        if not _has_council_role(interaction.user):
            await interaction.response.send_message("You do not have permission to use this command.", ephemeral=True)
            return

        updated_rows = db.rename_canonical_game(old_name.strip(), new_name.strip())
        db.write_audit(action="RENAME_GAME", actor_id=str(interaction.user.id),
                       target_id=old_name.strip(), payload={"old_name": old_name.strip(), "new_name": new_name.strip(),
                                                             "records_updated": updated_rows})
        await interaction.response.send_message(
            f"✅ Renamed **{old_name.strip()}** → **{new_name.strip()}**. "
            f"{updated_rows} game record(s) updated.",
            ephemeral=True,
        )
