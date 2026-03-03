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

        # Review table: soft-deleted game instances (recoverable)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS game_instances_review (
            id INTEGER PRIMARY KEY,
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
            message_id TEXT,
            deleted_at_utc TEXT NOT NULL,
            deleted_by TEXT NOT NULL
        );
        """)

        # Audit log: every write operation recorded here
        conn.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp_utc TEXT NOT NULL,
            actor_discord_id TEXT NOT NULL,
            action TEXT NOT NULL,
            target_id TEXT,
            payload_json TEXT
        );
        """)

        # Game name normalization tables
        conn.execute("""
        CREATE TABLE IF NOT EXISTS canonical_games (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            canonical_name TEXT UNIQUE NOT NULL,
            added_at_utc TEXT NOT NULL,
            added_by TEXT NOT NULL
        );
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS game_name_aliases (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            raw_name TEXT UNIQUE NOT NULL,
            canonical_id INTEGER NOT NULL REFERENCES canonical_games(id),
            mapped_at_utc TEXT NOT NULL
        );
        """)


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
# Review / Soft-Delete
# ---------------------------

def move_game_to_review(game_id: int, deleted_by_discord_id: str) -> Optional[dict]:
    """Move a game_instance row to game_instances_review. Returns the moved row dict or None."""
    now_utc = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM game_instances WHERE id = ?", (game_id,)
        ).fetchone()
        if row is None:
            return None
        row_dict = dict(row)
        conn.execute("""
            INSERT INTO game_instances_review (
                id, timestamp_utc, local_date, split_id, game_name, duration_min,
                players_json, winners_json, pool_points, points_per_winner,
                review_flag, notes, logged_by, channel_id, message_id,
                deleted_at_utc, deleted_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            row_dict["id"], row_dict["timestamp_utc"], row_dict["local_date"],
            row_dict["split_id"], row_dict["game_name"], row_dict["duration_min"],
            row_dict["players_json"], row_dict["winners_json"], row_dict["pool_points"],
            row_dict["points_per_winner"], row_dict["review_flag"], row_dict["notes"],
            row_dict["logged_by"], row_dict["channel_id"], row_dict["message_id"],
            now_utc, deleted_by_discord_id,
        ))
        conn.execute("DELETE FROM game_instances WHERE id = ?", (game_id,))
    return row_dict


def restore_game_from_review(game_id: int) -> Optional[dict]:
    """Restore a game from game_instances_review back to game_instances. Returns the row or None."""
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM game_instances_review WHERE id = ?", (game_id,)
        ).fetchone()
        if row is None:
            return None
        row_dict = dict(row)
        conn.execute("""
            INSERT OR IGNORE INTO game_instances (
                id, timestamp_utc, local_date, split_id, game_name, duration_min,
                players_json, winners_json, pool_points, points_per_winner,
                review_flag, notes, logged_by, channel_id, message_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            row_dict["id"], row_dict["timestamp_utc"], row_dict["local_date"],
            row_dict["split_id"], row_dict["game_name"], row_dict["duration_min"],
            row_dict["players_json"], row_dict["winners_json"], row_dict["pool_points"],
            row_dict["points_per_winner"], row_dict["review_flag"], row_dict["notes"],
            row_dict["logged_by"], row_dict["channel_id"], row_dict["message_id"],
        ))
        conn.execute("DELETE FROM game_instances_review WHERE id = ?", (game_id,))
    return row_dict


def fetch_review_games_for_split(split_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM game_instances_review WHERE split_id = ? ORDER BY id ASC",
            (split_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------
# Audit Log
# ---------------------------

def write_audit(action: str, actor_id: str, target_id: str = None, payload: dict = None) -> None:
    """Fire-and-forget audit write. Never raises."""
    try:
        now_utc = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
        payload_json = json.dumps(payload) if payload is not None else None
        with connect() as conn:
            conn.execute("""
                INSERT INTO audit_log (timestamp_utc, actor_discord_id, action, target_id, payload_json)
                VALUES (?, ?, ?, ?, ?)
            """, (now_utc, str(actor_id), action, str(target_id) if target_id is not None else None, payload_json))
    except Exception:
        pass


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


# ---------------------------
# Game Name Normalization
# ---------------------------

def normalize_game_name(raw_name: str) -> str:
    """
    Look up raw_name (case-insensitive) in game_name_aliases.
    Returns the canonical name if found, else raw_name unchanged.
    """
    with connect() as conn:
        row = conn.execute("""
            SELECT cg.canonical_name
            FROM game_name_aliases gna
            JOIN canonical_games cg ON gna.canonical_id = cg.id
            WHERE LOWER(gna.raw_name) = LOWER(?)
        """, (raw_name.strip(),)).fetchone()
    return str(row["canonical_name"]) if row else raw_name.strip()


def add_canonical_game(name: str, actor_id: str) -> bool:
    """
    Register a canonical game name. Returns True if inserted, False if already exists.
    """
    now_utc = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    try:
        with connect() as conn:
            conn.execute(
                "INSERT INTO canonical_games (canonical_name, added_at_utc, added_by) VALUES (?, ?, ?)",
                (name.strip(), now_utc, str(actor_id)),
            )
        return True
    except sqlite3.IntegrityError:
        return False


def map_game_alias(raw_name: str, canonical_name: str, actor_id: str) -> Optional[str]:
    """
    Map raw_name -> canonical_name in game_name_aliases.
    Returns error message string on failure, None on success.
    """
    now_utc = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
    with connect() as conn:
        row = conn.execute(
            "SELECT id FROM canonical_games WHERE LOWER(canonical_name) = LOWER(?)",
            (canonical_name.strip(),),
        ).fetchone()
        if row is None:
            return f"No canonical game named '{canonical_name}' found. Use `/addgame` first."
        canonical_id = row["id"]
        try:
            conn.execute("""
                INSERT INTO game_name_aliases (raw_name, canonical_id, mapped_at_utc)
                VALUES (?, ?, ?)
                ON CONFLICT(raw_name) DO UPDATE SET
                    canonical_id=excluded.canonical_id,
                    mapped_at_utc=excluded.mapped_at_utc
            """, (raw_name.strip(), canonical_id, now_utc))
        except Exception as e:
            return str(e)
    return None


def rename_canonical_game(old_name: str, new_name: str) -> int:
    """
    Rename a canonical game and update all game_instances rows.
    Returns the number of game_instances rows updated.
    """
    with connect() as conn:
        conn.execute(
            "UPDATE canonical_games SET canonical_name = ? WHERE LOWER(canonical_name) = LOWER(?)",
            (new_name.strip(), old_name.strip()),
        )
        cur = conn.execute(
            "UPDATE game_instances SET game_name = ? WHERE LOWER(game_name) = LOWER(?)",
            (new_name.strip(), old_name.strip()),
        )
        return cur.rowcount
