"""Clock times, as minutes after midnight."""

import re

MINUTES_PER_DAY = 24 * 60
_TIME = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*([ap])?\.?(m\.?)?\s*$", re.IGNORECASE)


def parse_time(text: str) -> int:
    """Minutes after midnight for "9:30", "21:30", "9:30pm" or "9:30 a.m."."""
    m = _TIME.match(text)
    if not m or (m[3] is None) != (m[4] is None):
        raise ValueError(f"not a clock time: {text!r}")
    hour, minute = int(m[1]), int(m[2])
    if minute > 59:
        raise ValueError(f"bad minutes in {text!r}")
    if m[3]:
        if not 1 <= hour <= 12:
            raise ValueError(f"bad hour for a 12-hour time: {text!r}")
        hour %= 12
        if m[3].lower() == "p":
            hour += 12
    elif hour > 23:
        raise ValueError(f"bad hour in {text!r}")
    return hour * 60 + minute


def format_time(minutes: int) -> str:
    minutes %= MINUTES_PER_DAY
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def minutes_between(start: int, end: int) -> int:
    """Minutes from `start` to `end`. An end at or before the start is on the next day."""
    if end <= start:
        end += MINUTES_PER_DAY
    return end - start
