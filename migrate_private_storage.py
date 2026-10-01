"""Controlled local cutover. Never stop processes or rewrite source history."""
import argparse
import ast
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
from config import PROJECT_ROOT, LOCAL_CONFIG, get_private_data_dir


def running_bot_count():
    if os.name != "nt":
        raise RuntimeError("Process safety verification is currently implemented for Windows only")
    script = r"Get-CimInstance Win32_Process -ErrorAction Stop -Filter \"Name = 'python.exe' OR Name = 'pythonw.exe'\" | Where-Object { $_.ProcessId -ne $env:SCOREKEEPER_CUTOVER_PID -and $_.CommandLine -match '(?i)(?:^|[\s\x22\\/])bot\.py(?:[\s\x22]|$)' } | Measure-Object | Select-Object -ExpandProperty Count"
    script = script.replace(r'\"', '"')
    environment = dict(os.environ, SCOREKEEPER_CUTOVER_PID=str(os.getpid()))
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], env=environment, capture_output=True, text=True)
    if result.returncode or not result.stdout.strip().isdecimal():
        raise RuntimeError("Unable to verify that the bot is stopped; cutover refused")
    return int(result.stdout.strip())


def database_digest(conn):
    result = {}
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        quoted = '"' + name.replace('"', '""') + '"'
        rows = conn.execute(f"SELECT * FROM {quoted}").fetchall()
        result[name] = hashlib.sha256(repr(sorted(map(repr, rows))).encode()).hexdigest()
    return result


def apply_cutover():
    root = get_private_data_dir()
    if running_bot_count():
        raise RuntimeError("The bot is running. Cutover refused; no files moved and no processes stopped.")
    original_db = PROJECT_ROOT / "data" / "bot.db"
    if not original_db.is_file():
        raise RuntimeError("Original live database is missing; cutover refused")
    stamp = datetime.now(timezone.utc).strftime("cutover-%Y%m%dT%H%M%S%fZ")
    backup = root.parent / "backups" / stamp
    backup.mkdir(parents=True, exist_ok=False)
    snapshot = backup / "bot.db"
    with closing(sqlite3.connect(original_db.as_uri() + "?mode=ro", uri=True)) as source, closing(sqlite3.connect(snapshot)) as dest:
        source.execute("BEGIN")
        digest = database_digest(source)
        source.backup(dest)
        if dest.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or database_digest(dest) != digest:
            raise RuntimeError("Database verification failed; originals retained")
        source.rollback()
    old_private = root / "bot.db"
    if old_private.exists():
        shutil.copy2(old_private, backup / "previous-private-bot.db")
    moves = [(p, root / "schedules" / p.name) for p in PROJECT_ROOT.glob("*_gamenights.csv")]
    moves += [(p, root / "legacy" / p.name) for p in (PROJECT_ROOT / "data" / "legacy").glob("*.csv")]
    if (PROJECT_ROOT / "aliases.csv").exists():
        moves.append((PROJECT_ROOT / "aliases.csv", root / "aliases.csv"))
    manifest = {}
    for source, target in moves:
        saved = backup / source.relative_to(PROJECT_ROOT)
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, saved)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            previous = backup / "previous-private" / target.relative_to(root)
            previous.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, previous)
        shutil.copy2(source, target)
        expected = hashlib.sha256(source.read_bytes()).hexdigest()
        if expected != hashlib.sha256(target.read_bytes()).hexdigest() or expected != hashlib.sha256(saved.read_bytes()).hexdigest():
            raise RuntimeError("Private input verification failed; originals retained")
        manifest[source.relative_to(PROJECT_ROOT).as_posix()] = expected
    if running_bot_count():
        raise RuntimeError("Bot started during cutover; originals retained and storage remains pending")
    temp = root / "bot.db.cutover"
    shutil.copy2(snapshot, temp)
    temp.replace(old_private)
    pending_module = PROJECT_ROOT / "pending_cutover" / "announcements.py"
    if pending_module.is_file():
        replacement = pending_module.read_text(encoding="utf-8")
        ast.parse(replacement)
        original_module = PROJECT_ROOT / "announcements.py"
        module_backup = backup / "source" / "announcements.py"
        module_backup.parent.mkdir(parents=True, exist_ok=True)
        if original_module.exists():
            shutil.copy2(original_module, module_backup)
        module_temp = original_module.with_suffix(".py.cutover")
        module_temp.write_text(replacement, encoding="utf-8", newline="\n")
        module_temp.replace(original_module)
    (backup / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    config = json.loads(LOCAL_CONFIG.read_text(encoding="utf-8"))
    config["storage_ready"] = True
    candidate = LOCAL_CONFIG.with_name(LOCAL_CONFIG.name + ".tmp")
    candidate.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    candidate.replace(LOCAL_CONFIG)
    # Original repo-local files are left intact and ignored as a recovery safety net.
    # They may be archived separately after restart and private-storage verification.
    print("Private storage is ready. Verified originals and previous private copies are retained. Bot was not started.")


def main():
    parser = argparse.ArgumentParser(description="Verify or complete private storage after the bot is stopped")
    parser.add_argument("--apply", action="store_true", help="Copy the latest data and enable private storage; requires stopped bot")
    args = parser.parse_args()
    try:
        if args.apply:
            apply_cutover()
        else:
            count = running_bot_count()
            print(f"Bot-related Python processes: {count}. Cutover {'blocked' if count else 'can be reviewed'}; no changes made.")
    except RuntimeError as exc:
        print(str(exc))
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
