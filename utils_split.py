"""
utils_split.py
Defines the split rules and split-based filenames.

Split logic:
- Split1: Jan-Jun
- Split2: Jul-Dec

Best practice:
- Keep “business rules” (split definition) in one place.
"""

from datetime import date as date_type

def determine_split(date_str: str) -> str:
    """Return 'Split1' or 'Split2' based on MM/DD/YYYY."""
    month, day, year = map(int, date_str.split("/"))
    return "Split1" if 1 <= month <= 6 else "Split2"

def score_filename_for(date_str: str) -> str:
    """Return e.g. '2026_Split1.csv' based on a MM/DD/YYYY date string."""
    year = int(date_str.split("/")[-1])
    split = determine_split(date_str)
    return f"{year}_{split}.csv"

def schedule_filename_for(target_date: date_type) -> str:
    """Return e.g. '2026_Split1_gamenights.csv' based on a date."""
    date_str = target_date.strftime("%m/%d/%Y")
    year = target_date.year
    split = determine_split(date_str)
    return f"{year}_{split}_gamenights.csv"
