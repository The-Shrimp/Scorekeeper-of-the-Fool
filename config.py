"""Secrets and local private-storage configuration; values never belong in Git."""
import json
import os
from pathlib import Path
from functools import lru_cache
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
LOCAL_CONFIG = PROJECT_ROOT / ".private-config.json"


def _local_config() -> dict:
    if not LOCAL_CONFIG.is_file():
        return {}
    value = json.loads(LOCAL_CONFIG.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError("Invalid local private-storage configuration")
    return value


def get_private_data_dir() -> Path:
    value = os.getenv("SCOREKEEPER_PRIVATE_DATA_DIR") or _local_config().get("private_data_dir")
    if not value:
        raise RuntimeError("Configure private_data_dir in ignored .private-config.json or SCOREKEEPER_PRIVATE_DATA_DIR")
    root = Path(value).expanduser().resolve()
    if root == PROJECT_ROOT or PROJECT_ROOT in root.parents:
        raise RuntimeError("Private storage must be outside the repository")
    if not root.is_dir():
        raise RuntimeError("Configured private-storage directory does not exist")
    return root


def require_private_storage_ready() -> Path:
    root = get_private_data_dir()
    if not os.getenv("SCOREKEEPER_PRIVATE_DATA_DIR") and _local_config().get("storage_ready") is not True:
        raise RuntimeError("Private-storage cutover is pending. Stop the bot and run migrate_private_storage.py --apply before restarting.")
    return root


def load_config() -> dict:
    load_dotenv(PROJECT_ROOT / ".env")
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN is missing. Add it to your .env or environment.")
    return {"DISCORD_BOT_TOKEN": token}


def get_allowed_guild_id() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    raw = os.getenv("SERVER_ID", "")
    if not raw.isdecimal() or int(raw) <= 0:
        raise RuntimeError("SERVER_ID must identify the authorized guild")
    return int(raw)


@lru_cache(maxsize=1)
def runtime_storage_paths():
    """Freeze one storage selection for this process; never switch a live bot."""
    local = _local_config()
    if os.getenv("SCOREKEEPER_PRIVATE_DATA_DIR") or local.get("storage_ready") is True:
        root = require_private_storage_ready()
        return root / "bot.db", root / "legacy", root / "schedules"
    if local.get("legacy_runtime") is True and local.get("storage_ready") is False:
        database = PROJECT_ROOT / "data" / "bot.db"
        legacy = PROJECT_ROOT / "data" / "legacy"
        if not database.is_file() or not legacy.is_dir():
            raise RuntimeError("Pending cutover requires the original live data; no empty database will be created")
        return database, legacy, PROJECT_ROOT
    raise RuntimeError("Private-storage cutover is pending; runtime storage is not enabled")


def get_runtime_database_path() -> Path:
    return runtime_storage_paths()[0]


def get_runtime_legacy_dir() -> Path:
    return runtime_storage_paths()[1]


def get_runtime_schedules_dir() -> Path:
    return runtime_storage_paths()[2]
