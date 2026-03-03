"""
scoring_engine.py
Pure rules: validation, rounding, points pool math, and split aggregation.

Best practice:
- Keep “business logic” pure (no Discord objects, no DB code).
- This makes it testable and safe to refactor.
"""

import json
import math
from dataclasses import dataclass
from datetime import datetime, date as date_type
from typing import Optional

import scoring_config as cfg

@dataclass(frozen=True)
class GameComputation:
    duration_min: int
    pool_points: float
    points_per_winner: float
    review_flag: int

def round_minutes_to_nearest_15(raw_minutes: int) -> int:
    """
    Round to nearest 15 minutes.
    Example: 22 -> 15, 23 -> 30
    """
    m = max(0, int(raw_minutes))
    rounded = int(round(m / cfg.ROUND_MINUTES_TO) * cfg.ROUND_MINUTES_TO)
    return max(cfg.MIN_DURATION_MINUTES, rounded)

def compute_points(duration_min: int, p: int, w: int) -> GameComputation:
    """
    Implements:
      pool = R * t * (p - 1) * (p / w) ^ alpha
      points_per_winner = pool / w

    review_flag if duration > 120 minutes.
    """
    t = duration_min
    pool = cfg.R_POINTS_PER_MINUTE * t * (p - 1) * ((p / w) ** cfg.ALPHA)
    ppw = pool / w
    # store precision, display separately
    ppw = round(ppw, cfg.STORE_DECIMALS)
    pool = round(pool, cfg.STORE_DECIMALS)
    review_flag = 1 if t > cfg.REVIEW_THRESHOLD_MINUTES else 0
    return GameComputation(duration_min=t, pool_points=pool, points_per_winner=ppw, review_flag=review_flag)

def validate_game_instance(players: list[int], winners: list[int], duration_min: int) -> Optional[str]:
    """
    Returns error message if invalid, otherwise None.
    """
    if duration_min <= 0:
        return "Duration must be greater than 0."

    if len(players) < 2:
        return "A game must have at least 2 players."

    if len(winners) < 1:
        return "A game must have at least 1 winner."

    if len(winners) > len(players):
        return "Number of winners cannot exceed number of players."

    if len(set(players)) != len(players):
        return "Duplicate players are not allowed."

    if len(set(winners)) != len(winners):
        return "Duplicate winners are not allowed."

    player_set = set(players)
    for w in winners:
        if w not in player_set:
            return "Every winner must also be listed in the players roster."

    return None

def display_points(points: float) -> str:
    """
    You requested:
    - store to 2 decimals
    - display as integer
    """
    if cfg.DISPLAY_ROUNDING == "int":
        return str(int(round(points)))
    return f"{points:.2f}"

@dataclass
class PlayerSplitStats:
    discord_id: int
    total_points: float
    total_minutes: int
    nights_attended: int

    @property
    def hours(self) -> float:
        return self.total_minutes / 60.0

    @property
    def raw_efficiency(self) -> float:
        return (self.total_points / self.hours) if self.hours > 0 else 0.0

def aggregate_split(game_rows: list[dict]) -> dict[int, PlayerSplitStats]:
    """
    Aggregate raw game instances into per-player totals.
    Expects DB rows containing:
      - duration_min
      - local_date
      - players_json
      - winners_json
      - points_per_winner
    """
    points: dict[int, float] = {}
    minutes: dict[int, int] = {}
    nights_by_player: dict[int, set[str]] = {}

    for row in game_rows:
        t = int(row["duration_min"])
        local_date = str(row["local_date"])
        players = json.loads(row["players_json"])
        winners = set(json.loads(row["winners_json"]))
        ppw = float(row["points_per_winner"])

        for pid in players:
            pid = int(pid)
            minutes[pid] = minutes.get(pid, 0) + t
            nights_by_player.setdefault(pid, set()).add(local_date)

        for wid in winners:
            wid = int(wid)
            points[wid] = points.get(wid, 0.0) + ppw

    result: dict[int, PlayerSplitStats] = {}
    all_ids = set(minutes.keys()) | set(points.keys()) | set(nights_by_player.keys())
    for pid in all_ids:
        result[pid] = PlayerSplitStats(
            discord_id=pid,
            total_points=round(points.get(pid, 0.0), cfg.STORE_DECIMALS),
            total_minutes=int(minutes.get(pid, 0)),
            nights_attended=len(nights_by_player.get(pid, set())),
        )
    return result

def compute_player_detail(player_id: int, game_rows: list[dict]) -> dict:
    """
    Extended per-player stats derived from raw game rows.
    Returns games_played, wins, win_rate, most_played_game, game_counts.
    """
    games_played = 0
    wins = 0
    game_counts: dict[str, int] = {}

    for row in game_rows:
        players = [int(p) for p in json.loads(row["players_json"])]
        if player_id not in players:
            continue
        games_played += 1
        game_name = str(row["game_name"])
        game_counts[game_name] = game_counts.get(game_name, 0) + 1
        winners = {int(w) for w in json.loads(row["winners_json"])}
        if player_id in winners:
            wins += 1

    win_rate = wins / games_played if games_played > 0 else 0.0
    most_played_game = max(game_counts, key=game_counts.get) if game_counts else "N/A"

    return {
        "games_played": games_played,
        "wins": wins,
        "win_rate": win_rate,
        "most_played_game": most_played_game,
        "game_counts": game_counts,
    }


def compute_split_summary(game_rows: list[dict]) -> dict:
    """
    Split-level aggregate stats for /splitstats.
    Returns total_games, total_players, total_hours, most played by count,
    most played by collective time, and busiest night.
    """
    total_minutes = 0
    all_players: set[int] = set()
    game_counts: dict[str, int] = {}
    game_minutes: dict[str, int] = {}
    date_counts: dict[str, int] = {}

    for row in game_rows:
        dur = int(row["duration_min"])
        total_minutes += dur
        for pid in json.loads(row["players_json"]):
            all_players.add(int(pid))
        game_name = str(row["game_name"])
        game_counts[game_name] = game_counts.get(game_name, 0) + 1
        game_minutes[game_name] = game_minutes.get(game_name, 0) + dur
        local_date = str(row["local_date"])
        date_counts[local_date] = date_counts.get(local_date, 0) + 1

    most_played_game = max(game_counts, key=game_counts.get) if game_counts else "N/A"
    most_played_count = game_counts.get(most_played_game, 0)

    most_time_game = max(game_minutes, key=game_minutes.get) if game_minutes else "N/A"
    most_time_hours = game_minutes.get(most_time_game, 0) / 60.0 if most_time_game != "N/A" else 0.0

    busiest_night_raw = max(date_counts, key=date_counts.get) if date_counts else None
    busiest_night_games = date_counts.get(busiest_night_raw, 0) if busiest_night_raw else 0

    # Format busiest night date nicely (ISO -> MM/DD/YYYY)
    if busiest_night_raw:
        try:
            from datetime import datetime as _dt
            busiest_night = _dt.strptime(busiest_night_raw, "%Y-%m-%d").strftime("%m/%d/%Y")
        except ValueError:
            busiest_night = busiest_night_raw
    else:
        busiest_night = "N/A"

    return {
        "total_games": len(game_rows),
        "total_players": len(all_players),
        "total_hours": total_minutes / 60.0,
        "most_played_game": most_played_game,
        "most_played_count": most_played_count,
        "most_time_game": most_time_game,
        "most_time_hours": most_time_hours,
        "busiest_night": busiest_night,
        "busiest_night_games": busiest_night_games,
    }


def compute_leaderboard(stats: dict[int, PlayerSplitStats]):
    """
    Returns:
      eligible_ranked: list of dict entries with computed fields
      ineligible: list of dict entries with missing requirements

    Implements:
      Eligibility: hours >= 7 and nights >= 3
      E0: average efficiency across eligible players
      E_adj shrinkage with H0
      AdjustedTotal = E_adj * H
    """
    eligible = []
    ineligible = []

    for pid, s in stats.items():
        if s.hours >= cfg.MIN_ELIGIBLE_HOURS and s.nights_attended >= cfg.MIN_ELIGIBLE_NIGHTS:
            eligible.append(s)
        else:
            missing = []
            if s.hours < cfg.MIN_ELIGIBLE_HOURS:
                missing.append(f"needs {cfg.MIN_ELIGIBLE_HOURS - s.hours:.1f} more hours")
            if s.nights_attended < cfg.MIN_ELIGIBLE_NIGHTS:
                missing.append(f"needs {cfg.MIN_ELIGIBLE_NIGHTS - s.nights_attended} more nights")
            ineligible.append({
                "discord_id": pid,
                "total_points": s.total_points,
                "hours": s.hours,
                "nights": s.nights_attended,
                "missing": ", ".join(missing) if missing else "ineligible",
                "raw_efficiency": s.raw_efficiency
            })

    # Baseline efficiency E0 across eligible players
    if eligible:
        e0 = sum(p.raw_efficiency for p in eligible) / len(eligible)
    else:
        e0 = 0.0

    ranked = []
    for p in eligible:
        H = p.hours
        E = p.raw_efficiency
        H0 = cfg.H0_HOURS
        E_adj = ((e0 * H0) + (E * H)) / (H0 + H) if (H0 + H) > 0 else 0.0
        adjusted_total = E_adj * H
        ranked.append({
            "discord_id": p.discord_id,
            "total_points": p.total_points,
            "hours": H,
            "nights": p.nights_attended,
            "raw_efficiency": E,
            "e_adj": E_adj,
            "adjusted_total": adjusted_total,
        })

    ranked.sort(key=lambda r: (
        r["adjusted_total"],
        r["total_points"],
        r["nights"],
        r["hours"],
    ), reverse=True)

    return ranked, ineligible, e0
