"""
migrate_schedule_to_db.py

One-time script that imports all existing *_gamenights.csv files into the
game_nights and rsvps tables in data/bot.db.

How it works:
  1. Reads every *_gamenights.csv in the project root.
  2. Builds a reverse alias map (alias → discord_id) from player_aliases in the DB.
  3. Upserts each row into game_nights.
  4. Maps Attendees → 'yes', Possible Attendees → 'maybe', Unavailable → 'unavailable'
     to rsvps rows (matching by alias).
  5. Calls resolve_night_attendance() for every date so game participants are also
     marked as 'yes' and Social Night logic runs.

Run once from the project root:
    python migrate_schedule_to_db.py
"""

import glob
import sys
from datetime import datetime

import polars as pl

import db
from split_ids import split_id_for_date

# Players who appear in legacy CSVs under a display name rather than a bot alias.
# Format: { "name as it appears in csv (lowercase)": "discord_id_string" }
# Add entries here whenever a name can't be matched automatically.
MANUAL_OVERRIDES: dict[str, str] = {
    "nainoa da luz": "277946889339404288",
    "sircray": "1093078815133151313",
    "xavier aguon": "1093078815133151313",
}

# Primary alias to register in player_aliases for each new discord_id.
# Only used when the ID isn't already in player_aliases.
MANUAL_ALIASES: dict[str, str] = {
    "277946889339404288": "Nainoa da Luz",
    "1093078815133151313": "Sircray",
}


def build_reverse_alias_map() -> dict[str, str]:
    """Return {alias_lower: discord_id} from the player_aliases table + manual overrides."""
    alias_map = db.load_alias_map()  # {discord_id: alias}
    reverse = {alias.lower(): discord_id for discord_id, alias in alias_map.items()}
    # Manual overrides take precedence
    reverse.update(MANUAL_OVERRIDES)
    return reverse


def register_manual_aliases() -> None:
    """Upsert MANUAL_ALIASES into player_aliases so the bot recognises these players."""
    existing = db.load_alias_map()  # {discord_id: alias}
    for discord_id, alias in MANUAL_ALIASES.items():
        if discord_id not in existing:
            db.upsert_alias(int(discord_id), alias)
            print(f"  Registered alias: {discord_id} -> {alias!r}")


def parse_date_to_iso(date_str: str) -> str | None:
    """Convert MM/DD/YYYY → YYYY-MM-DD, or return None on failure."""
    try:
        dt = datetime.strptime(date_str.strip(), "%m/%d/%Y")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return None


def parse_names(raw: str | None) -> list[str]:
    """Split a comma-separated alias string into a list of stripped names."""
    if not raw or not str(raw).strip():
        return []
    return [n.strip() for n in str(raw).split(",") if n.strip()]


def main() -> None:
    db.init_db()
    print("Registering manual aliases ...")
    register_manual_aliases()
    reverse_alias = build_reverse_alias_map()

    csv_files = sorted(glob.glob("*_gamenights.csv"))
    if not csv_files:
        print("No *_gamenights.csv files found in the current directory.")
        sys.exit(0)

    total_nights = 0
    total_rsvps = 0
    unmatched: set[str] = set()

    for csv_file in csv_files:
        print(f"Processing {csv_file} ...")
        try:
            df = pl.read_csv(csv_file, infer_schema_length=0)
        except Exception as exc:
            print(f"  ERROR reading {csv_file}: {exc}")
            continue

        for row in df.iter_rows(named=True):
            date_str = (row.get("Date") or "").strip()
            if not date_str:
                continue

            date_iso = parse_date_to_iso(date_str)
            if date_iso is None:
                print(f"  Skipping unrecognised date: {date_str!r}")
                continue

            dt = datetime.strptime(date_iso, "%Y-%m-%d").date()
            split = split_id_for_date(dt)

            raw_status = (row.get("Status") or "").strip()
            status = raw_status if raw_status else "Undecided"
            time_str = (row.get("Time") or "").strip() or None
            location = (row.get("Location") or "").strip() or None
            notes = (row.get("Notes") or "").strip() or None
            afterwards = (row.get("Afterwards Comments") or "").strip() or None

            db.upsert_game_night(
                date_iso=date_iso,
                split_id=split,
                status=status,
                time_str=time_str,
                location=location,
                notes=notes,
                afterwards_comments=afterwards,
            )
            total_nights += 1

            # Map RSVP columns to DB rsvp_status values
            rsvp_fields = [
                ("Attendees", "yes"),
                ("Possible Attendees", "maybe"),
                ("Unavailable", "unavailable"),
            ]
            for field, rsvp_status in rsvp_fields:
                names = parse_names(row.get(field))
                for name in names:
                    discord_id = reverse_alias.get(name.lower())
                    if discord_id:
                        db.upsert_rsvp(date_iso, discord_id, rsvp_status)
                        total_rsvps += 1
                    else:
                        unmatched.add(name)

    # Run attendance resolution for every imported night
    print("\nResolving attendance for all imported game nights ...")
    all_nights = db.fetch_all_game_nights()
    for night in all_nights:
        result = db.resolve_night_attendance(night["date_iso"])
        if result == "Social Night":
            print(f"  {night['date_iso']} -> marked as Social Night")

    print(f"\nDone. Imported {total_nights} game nights, {total_rsvps} RSVP records.")

    if unmatched:
        print(
            f"\nThe following names had no matching discord_id in player_aliases "
            f"and were skipped ({len(unmatched)} unique):"
        )
        for name in sorted(unmatched):
            print(f"  - {name!r}")
        print(
            "These players may not have set an alias via /setalias. "
            "Their attendance has not been recorded in the DB."
        )


if __name__ == "__main__":
    main()
