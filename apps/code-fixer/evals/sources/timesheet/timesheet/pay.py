"""Pay stubs: regular and overtime pay for each pay week."""

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from .weeks import group_by_week

OVERTIME_AFTER = Decimal(40)      # hours in a pay week
OVERTIME_RATE = Decimal("1.5")
CENT = Decimal("0.01")


@dataclass(frozen=True)
class PayStub:
    week_of: date                 # the first day of the pay week
    regular_hours: Decimal
    overtime_hours: Decimal
    gross: Decimal                # dollars, rounded to the cent


def pay_stubs(shifts, hourly_rate, starts_on: str = "sunday") -> list:
    """One stub per pay week that has shifts, oldest first."""
    rate = Decimal(str(hourly_rate))
    stubs = []
    for week_of, week in group_by_week(shifts, starts_on).items():
        hours = sum((s.hours for s in week), Decimal(0))
        regular = min(hours, OVERTIME_AFTER)
        overtime = hours - regular
        gross = (regular * rate + overtime * rate * OVERTIME_RATE).quantize(CENT, rounding=ROUND_HALF_UP)
        stubs.append(PayStub(week_of, regular, overtime, gross))
    return stubs
