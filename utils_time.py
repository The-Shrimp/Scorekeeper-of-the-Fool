"""
utils_time.py
Time parsing and “upcoming Saturday at default time”.

Best practice:
- Keep input parsing isolated; it reduces bugs and makes commands cleaner.
"""

from datetime import datetime, timedelta

def get_upcoming_saturday_at_default_time() -> datetime:
    """Return a datetime for the upcoming Saturday at 5 PM local time."""
    now = datetime.now()
    days_ahead = (5 - now.weekday()) % 7  # Saturday=5
    if days_ahead == 0 and now.hour >= 17:
        days_ahead = 7
    target = now + timedelta(days=days_ahead)
    return target.replace(hour=17, minute=0, second=0, microsecond=0)

def parse_time_input(value: str):
    """
    Parse a time string like '5pm', '7:30 pm', '19:00'.
    Returns (display_time_str, hour24, minute) or (None, None, None).
    """
    value = (value or "").strip().lower()
    if not value:
        return None, None, None

    value = value.replace(" ", "")
    try:
        if "am" in value or "pm" in value:
            fmt = "%I%p" if ":" not in value else "%I:%M%p"
            t = datetime.strptime(value, fmt)
        else:
            fmt = "%H:%M" if ":" in value else "%H"
            t = datetime.strptime(value, fmt)

        display = t.strftime("%I:%M %p").lstrip("0")
        return display, t.hour, t.minute
    except ValueError:
        return None, None, None
