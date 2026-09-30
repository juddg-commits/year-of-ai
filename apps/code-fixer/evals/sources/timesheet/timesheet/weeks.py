"""Pay weeks: which week a shift counts toward."""

from collections import defaultdict
from datetime import date, timedelta

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def week_start(day: date, starts_on: str = "sunday") -> date:
    """The first day of the pay week that contains `day`."""
    offset = (day.weekday() - WEEKDAYS.index(starts_on.lower())) % 7
    return day - timedelta(days=offset)


def group_by_week(shifts, starts_on: str = "sunday") -> dict:
    """Shifts by the first day of their pay week, in date order. An overnight shift counts
    toward the week of the day it started."""
    weeks = defaultdict(list)
    for shift in sorted(shifts, key=lambda s: (s.day, s.start)):
        weeks[week_start(shift.day, starts_on)].append(shift)
    return dict(sorted(weeks.items()))
