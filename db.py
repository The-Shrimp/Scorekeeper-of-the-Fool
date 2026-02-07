"""
db.py
SQLite access layer for game instances (authoritative scoring history) + aliases.

Best practices:
- schema created automatically
- parameterized queries
- JSON stored as TEXT for list fields
"""

import os
import sqlite3
import json
from datetime import datetime
from typing import Optional, Any

DB_PATH = os.path.join("data", "bot.db")


def _ensure_data_dir() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)


def connect() -> sqlite3.Connection:
    _ensure_data_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS game_instances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp_utc TEXT NOT NULL,
            local_date TEXT NOT NULL,
            split_id TEXT NOT NULL,
            game_name TEXT NOT NULL,
            duration_min INTEGER NOT NULL,
            players_json TEXT NOT NULL,
            winners_json TEXT NOT NULL,
            pool_points REAL NOT NULL,
            points_per_winner REAL NOT NULL,
            review_flag INTEGER NOT NULL,
            notes TEXT,
            logged_by TEXT NOT NULL,
            channel_id TEXT,
            message_id TEXT
        );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_game_split ON game_instances(split_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_game_local_date ON game_instances(local_date);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_game_logged_by ON game_instances(logged_by);")

        # Aliases in DB (DiscordID -> Alias)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS player_aliases (
            discord_id TEXT PRIMARY KEY,
            alias TEXT NOT NULL,
            updated_at_utc TEXT NOT NULL
        );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alias_updated ON player_aliases(updated_at_utc);")


# ---------------------------
# Game Instances
# ---------------------------

def insert_game_instance(
    *,
    timestamp_utc: str,
    local_date: str,
    split_id: str,
    game_name: str,
    duration_min: int,
    players: list[int],
    winners: list[int],
    pool_points: float,
    points_per_winner: float,
    review_flag: int,
    notes: str,
    logged_by: int,
    channel_id: Optional[int],
    message_id: Optional[int],
) -> int:
    players_json = json.dumps(players)
    winners_json = json.dumps(winners)

    with connect() as conn:
        cur = conn.execute("""
            INSERT INTO game_instances (
                timestamp_utc, local_date, split_id, game_name, duration_min,
                players_json, winners_json, pool_points, points_per_winner,
                review_flag, notes, logged_by, channel_id, message_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            timestamp_utc, local_date, split_id, game_name, duration_min,
            players_json, winners_json, pool_points, points_per_winner,
            review_flag, notes, str(logged_by),
            str(channel_id) if channel_id else None,
            str(message_id) if message_id else None
        ))
        return int(cur.lastrowid)


def delete_game_instance(game_id: int) -> bool:
    with connect() as conn:
        cur = conn.execute("DELETE FROM game_instances WHERE id = ?", (game_id,))
        return cur.rowcount > 0


def get_last_game_instance_id(split_id: Optional[str] = None) -> Optional[int]:
    with connect() as conn:
        if split_id:
            row = conn.execute(
                "SELECT id FROM game_instances WHERE split_id = ? ORDER BY id DESC LIMIT 1",
                (split_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id FROM game_instances ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return int(row["id"]) if row else None


def fetch_game_instances_for_split(split_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM game_instances WHERE split_id = ? ORDER BY id ASC",
            (split_id,),
        ).fetchall()

    return [dict(r) for r in rows]


# ---------------------------
# Aliases (DB-backed)
# ---------------------------

def upsert_alias(discord_id: int, alias: str) -> None:
    did = str(discord_id)
    a = (alias or "").strip()
    if not a:
        return

    now_utc = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    with connect() as conn:
        conn.execute("""
            INSERT INTO player_aliases (discord_id, alias, updated_at_utc)
            VALUES (?, ?, ?)
            ON CONFLICT(discord_id) DO UPDATE SET
                alias=excluded.alias,
                updated_at_utc=excluded.updated_at_utc
        """, (did, a, now_utc))


def get_alias(discord_id: int) -> Optional[str]:
    with connect() as conn:
        row = conn.execute(
            "SELECT alias FROM player_aliases WHERE discord_id = ?",
            (str(discord_id),),
        ).fetchone()
    return str(row["alias"]) if row else None


def load_alias_map() -> dict[str, str]:
    """Return dict: discord_id(str) -> alias(str)."""
    with connect() as conn:
        rows = conn.execute("SELECT discord_id, alias FROM player_aliases").fetchall()
    return {str(r["discord_id"]): str(r["alias"]) for r in rows}
