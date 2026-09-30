from datetime import date
from decimal import Decimal

from timesheet import Shift, parse_time, pay_stubs


def shift(day, start, end, break_minutes=0):
    return Shift(date.fromisoformat(day), parse_time(start), parse_time(end), break_minutes)


def test_a_week_without_overtime():
    shifts = [shift(f"2026-03-0{d}", "9:00", "5:00pm", 30) for d in range(2, 7)]   # Mon-Fri, 7.5 h each
    [stub] = pay_stubs(shifts, hourly_rate=20)
    assert stub.week_of == date(2026, 3, 1)   # weeks start on Sunday
    assert stub.regular_hours == Decimal("37.5")
    assert stub.overtime_hours == 0
    assert stub.gross == Decimal("750.00")


def test_overtime_in_a_week_that_starts_on_sunday():
    shifts = [shift("2026-03-01", "8:00", "16:00")]                                   # Sunday, 8 h
    shifts += [shift(f"2026-03-0{d}", "8:00", "17:30", 30) for d in range(2, 6)]     # Mon-Thu, 9 h each
    [stub] = pay_stubs(shifts, hourly_rate=20)
    assert stub.week_of == date(2026, 3, 1)
    assert (stub.regular_hours, stub.overtime_hours) == (40, 4)
    assert stub.gross == Decimal("920.00")   # 40 x 20 + 4 x 30


def test_overtime_starts_over_each_week():
    week1 = [shift(f"2026-03-0{d}", "8:00", "16:24") for d in range(2, 7)]           # 8.4 h x 5 = 42 h
    week2 = [shift(f"2026-03-{d:02d}", "8:00", "16:24") for d in range(9, 14)]
    stubs = pay_stubs(week1 + week2, hourly_rate=10)
    assert [s.week_of for s in stubs] == [date(2026, 3, 1), date(2026, 3, 8)]
    assert [s.overtime_hours for s in stubs] == [2, 2]
    assert [s.gross for s in stubs] == [Decimal("430.00"), Decimal("430.00")]


def test_an_overnight_shift_counts_in_the_week_it_started():
    shifts = [shift(f"2026-03-0{d}", "8:00", "17:30", 30) for d in range(2, 6)]      # Mon-Thu, 36 h
    shifts.append(shift("2026-03-07", "10:00pm", "6:00am"))                           # Saturday night, 8 h
    shifts.append(shift("2026-03-08", "10:00pm", "6:00am"))                           # Sunday night: next week
    first, second = pay_stubs(shifts, hourly_rate=20)
    assert (first.week_of, first.overtime_hours, first.gross) == (date(2026, 3, 1), 4, Decimal("920.00"))
    assert (second.week_of, second.regular_hours, second.gross) == (date(2026, 3, 8), 8, Decimal("160.00"))


def test_gross_is_rounded_once_per_week():
    shifts = [shift(f"2026-03-0{d}", "9:00", "17:18") for d in range(2, 7)]          # 8.3 h x 5 = 41.5 h
    [stub] = pay_stubs(shifts, hourly_rate="17.35")
    assert stub.gross == Decimal("733.04")   # 694.00 + 39.0375, rounded half up
