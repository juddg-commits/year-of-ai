"""Shifts: the rows of a timesheet."""

import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .clock import minutes_between, parse_time


@dataclass(frozen=True)
class Shift:
    """One worked shift. A shift that ends at or before its start time ran past midnight;
    it belongs to the day it started."""

    day: date
    start: int              # minutes after midnight
    end: int
    break_minutes: int = 0

    @property
    def minutes(self) -> int:
        worked = minutes_between(self.start, self.end) - self.break_minutes
        if worked < 0:
            raise ValueError(f"the break is longer than the shift on {self.day}")
        return worked

    @property
    def hours(self) -> Decimal:
        return Decimal(self.minutes) / 60


def load_shifts(lines) -> list:
    """Shifts from CSV lines with a header: date (YYYY-MM-DD), start, end, and an optional break in minutes."""
    shifts = []
    for line_no, row in enumerate(csv.DictReader(lines), start=2):
        try:
            shifts.append(Shift(
                day=date.fromisoformat(row["date"].strip()),
                start=parse_time(row["start"]),
                end=parse_time(row["end"]),
                break_minutes=int(row.get("break") or 0),
            ))
        except (KeyError, ValueError) as e:
            raise ValueError(f"line {line_no}: {e}") from None
    return shifts
