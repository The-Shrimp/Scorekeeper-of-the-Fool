"""
db.py
SQLite access layer for game instances (authoritative scoring history).

Best practices taught here:
- single database file (data/bot.db)
- schema created automatically
- parameterized queries (prevents subtle bugs and injection)
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
    """
    Open a connection to SQLite.

    Best practice:
    - Use row_factory for dict-like access
    - Keep connections short-lived in small bots (simple + safe)
    """
    _ensure_data_dir()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
    """Create tables if they don't exist."""
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
    """Insert a game instance and return its game_id."""
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
    """Delete a game instance by id. Returns True if a row was deleted."""
    with connect() as conn:
        cur = conn.execute("DELETE FROM game_instances WHERE id = ?", (game_id,))
        return cur.rowcount > 0

def get_last_game_instance_id(split_id: Optional[str] = None) -> Optional[int]:
    """Return the most recently inserted game instance id (optionally within a split)."""
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
    """Fetch all game instances for a split as a list of dicts."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM game_instances WHERE split_id = ? ORDER BY id ASC",
            (split_id,),
        ).fetchall()

    results: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        results.append(d)
    return results
