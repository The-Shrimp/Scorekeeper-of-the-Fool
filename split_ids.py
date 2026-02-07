"""
split_ids.py
Defines split_id formatting used in the database.

Your split rules:
- Split1: Jan–Jun
- Split2: Jul–Dec

We encode as: YYYY-S1 or YYYY-S2 (e.g., 2026-S1)
"""

from datetime import date as date_type, datetime

def split_id_for_date(d: date_type) -> str:
    if 1 <= d.month <= 6:
        return f"{d.year}-S1"
    return f"{d.year}-S2"

def current_split_id() -> str:
    return split_id_for_date(datetime.now().date())
