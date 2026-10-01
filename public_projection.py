"""Transitional aggregate-only export. Private raw inputs never become assets."""
import argparse
import csv
from decimal import Decimal, InvalidOperation
import json
import re
from pathlib import Path
from config import get_private_data_dir
from public_identity import load_private_identity_mappings, display_name_for_legacy


def read_legacy_totals(path: Path) -> dict[str, Decimal]:
    totals = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not {"PlayerID", "Score"}.issubset(reader.fieldnames or []):
            raise ValueError("Legacy source is missing required score columns")
        for row in reader:
            name = (row.get("PlayerID") or "").strip()
            if not name:
                raise ValueError("Legacy source has an empty player identity")
            try:
                score = Decimal(row["Score"])
            except (InvalidOperation, TypeError):
                raise ValueError("Legacy source has an invalid score") from None
            if not score.is_finite():
                raise ValueError("Legacy source has a non-finite score")
            totals[name] = totals.get(name, Decimal(0)) + score
    return totals


def build_projection() -> dict:
    root = get_private_data_dir()
    mappings = load_private_identity_mappings()
    splits = []
    # The verified live website is authoritative for already published totals.
    # Preserve distinct bot originals privately; do not silently merge conflicts.
    sources = {p.name: p for p in (root / "legacy").glob("*_Split*.csv")}
    sources.update({p.name: p for p in (root / "website-legacy-originals").glob("*_Split*.csv")})
    paths = [sources[name] for name in sorted(sources)]
    if not paths:
        raise RuntimeError("No private legacy score sources are available")
    for path in paths:
        match = re.fullmatch(r"(\d{4})_Split([12])\.csv", path.name)
        if not match:
            raise ValueError("Unexpected legacy score filename")
        players = []
        for raw_name, total in read_legacy_totals(path).items():
            label = display_name_for_legacy(raw_name, mappings)
            number = int(total) if total == total.to_integral_value() else float(total)
            players.append({"display_name": label, "total_score": number})
        # Public labels determine ties; private names never affect public ordering.
        players.sort(key=lambda row: (-row["total_score"], row["display_name"]))
        rows = [{"rank": i, **row} for i, row in enumerate(players, 1)]
        splits.append({"year": int(match[1]), "split": int(match[2]), "players": rows})
    projection = {"splits": splits}
    validate_projection(projection)
    return projection


def validate_projection(value: dict) -> None:
    if not isinstance(value, dict) or set(value) != {"splits"} or not isinstance(value["splits"], list):
        raise ValueError("Unexpected public projection fields")
    seen = set()
    for split in value["splits"]:
        if not isinstance(split, dict) or set(split) != {"year", "split", "players"}:
            raise ValueError("Unexpected public split fields")
        if type(split["year"]) is not int or not 2000 <= split["year"] <= 2100 or type(split["split"]) is not int or split["split"] not in (1, 2):
            raise ValueError("Invalid public split")
        key = (split["year"], split["split"])
        if key in seen or not isinstance(split["players"], list):
            raise ValueError("Duplicate or invalid public split")
        seen.add(key)
        for rank, row in enumerate(split["players"], 1):
            if not isinstance(row, dict) or set(row) != {"rank", "display_name", "total_score"}:
                raise ValueError("Unexpected public player fields")
            if type(row["rank"]) is not int or row["rank"] != rank or not isinstance(row["display_name"], str) or not row["display_name"].strip() or len(row["display_name"]) > 80 or any(ord(c) < 32 or ord(c) == 127 for c in row["display_name"]):
                raise ValueError("Invalid public player")
            score = row["total_score"]
            if type(score) not in (int, float) or not Decimal(str(score)).is_finite():
                raise ValueError("Invalid public score")


def write_projection(path: Path) -> dict:
    projection = build_projection()
    path = path.resolve()
    if path.suffix != ".json":
        raise ValueError("Public export must be a JSON file")
    if path == get_private_data_dir() or get_private_data_dir() in path.parents:
        raise ValueError("Export destination must be separate from private source storage")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(projection, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(path)
    return projection


def main():
    parser = argparse.ArgumentParser(description="Export only public aggregate scores from private legacy sources")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = write_projection(args.output)
    print(f"Exported {len(value['splits'])} public split summaries; private identities and records omitted.")


if __name__ == "__main__":
    main()
