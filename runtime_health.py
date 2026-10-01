"""Private local readiness marker; never includes tokens, rosters or notes."""
from datetime import datetime, timezone
import json
import os
from config import get_private_data_dir, runtime_storage_paths


def record_runtime_health(stage, *, command_sync_ok=None, startup_errors=0):
    root = get_private_data_dir()
    database, legacy, schedules = runtime_storage_paths()
    value = {
        "pid": os.getpid(), "stage": stage,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "database_path": str(database), "legacy_path": str(legacy),
        "schedules_path": str(schedules),
        "private_storage_selected": database == root / "bot.db",
        "command_sync_ok": command_sync_ok, "startup_errors": startup_errors,
    }
    candidate = root / "runtime-health.json.tmp"
    candidate.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    candidate.replace(root / "runtime-health.json")
