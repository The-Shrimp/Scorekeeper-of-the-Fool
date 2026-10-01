"""Public labels backed by an identity registry that stays in private storage."""
import json
import re
import sqlite3
from contextlib import closing
from config import get_private_data_dir


def load_private_identity_mappings() -> dict:
    path = get_private_data_dir() / "identity-mappings.json"
    if not path.is_file():
        raise RuntimeError("Private identity mappings are missing")
    value = json.loads(path.read_text(encoding="utf-8"))
    required = {"legacy_aliases", "manual_overrides", "manual_aliases"}
    if not isinstance(value, dict) or set(value) != required:
        raise RuntimeError("Invalid private identity-mapping structure")
    for mapping in value.values():
        if not isinstance(mapping, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()):
            raise RuntimeError("Invalid private identity mapping")
    return value


def _alias_or_none(alias, forbidden_id=None):
    if not isinstance(alias, str):
        return None
    clean = alias.strip()
    if not clean or len(clean) > 80 or any(ord(c) < 32 or ord(c) == 127 for c in clean):
        return None
    if re.fullmatch(r"Player #\d+", clean) or re.search(r"\d{17,20}", clean):
        return None
    if forbidden_id is not None and clean == str(forbidden_id):
        return None
    return clean


def public_label(identity_key: str, alias=None, forbidden_id=None) -> str:
    if not identity_key:
        raise ValueError("An internal identity key is required")
    path = get_private_data_dir() / "public-identities.db"
    with closing(sqlite3.connect(path, timeout=15)) as conn, conn:
        conn.execute("CREATE TABLE IF NOT EXISTS public_players (public_number INTEGER PRIMARY KEY AUTOINCREMENT, identity_key TEXT NOT NULL UNIQUE)")
        conn.execute("INSERT OR IGNORE INTO public_players(identity_key) VALUES (?)", (identity_key,))
        number = conn.execute("SELECT public_number FROM public_players WHERE identity_key = ?", (identity_key,)).fetchone()[0]
    return _alias_or_none(alias, forbidden_id) or f"Player #{number}"


def display_name_for_discord(discord_id, alias=None) -> str:
    return public_label(f"discord:{int(discord_id)}", alias, forbidden_id=discord_id)


def display_name_for_legacy(raw_name: str, mappings: dict) -> str:
    alias = mappings["legacy_aliases"].get(raw_name)
    discord_id = mappings["manual_overrides"].get(raw_name.casefold())
    key = f"discord:{discord_id}" if discord_id else "legacy:" + raw_name
    return public_label(key, alias, forbidden_id=discord_id)
