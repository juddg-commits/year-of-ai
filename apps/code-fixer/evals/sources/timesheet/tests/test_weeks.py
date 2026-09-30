from datetime import date

from timesheet import Shift, group_by_week, week_start


def test_week_start_for_weeks_that_start_on_monday():
    assert week_start(date(2026, 3, 2), "monday") == date(2026, 3, 2)
    assert week_start(date(2026, 3, 4), "monday") == date(2026, 3, 2)
    assert week_start(date(2026, 3, 8), "monday") == date(2026, 3, 2)
    assert week_start(date(2026, 3, 9), "monday") == date(2026, 3, 9)


def test_group_by_week_sorts_and_groups():
    late = Shift(date(2026, 3, 10), 540, 1020)
    early = Shift(date(2026, 3, 3), 540, 1020)
    earlier_same_day = Shift(date(2026, 3, 3), 300, 500)
    weeks = group_by_week([late, early, earlier_same_day], starts_on="monday")
    assert list(weeks) == [date(2026, 3, 2), date(2026, 3, 9)]
    assert weeks[date(2026, 3, 2)] == [earlier_same_day, early]
    assert weeks[date(2026, 3, 9)] == [late]
