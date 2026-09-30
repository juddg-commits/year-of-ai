"""Pay weeks that start on days other than Monday (a hidden test in the timesheet-week-start case)."""

from datetime import date, timedelta

from timesheet import Shift, group_by_week, week_start


def test_weeks_start_on_sunday_by_default():
    sunday = date(2026, 3, 1)
    for n in range(7):
        assert week_start(sunday + timedelta(days=n)) == sunday
    assert week_start(date(2026, 3, 8)) == date(2026, 3, 8)


def test_other_start_days():
    assert week_start(date(2026, 3, 3), "wednesday") == date(2026, 2, 25)
    assert week_start(date(2026, 3, 4), "wednesday") == date(2026, 3, 4)
    assert week_start(date(2026, 3, 6), "Saturday") == date(2026, 2, 28)
    assert week_start(date(2026, 3, 7), "saturday") == date(2026, 3, 7)


def test_saturday_and_sunday_are_different_weeks():
    saturday, sunday = Shift(date(2026, 3, 7), 540, 1020), Shift(date(2026, 3, 8), 540, 1020)
    assert list(group_by_week([sunday, saturday])) == [date(2026, 3, 1), date(2026, 3, 8)]
