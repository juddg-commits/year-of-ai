"""Shifts that run past midnight (a hidden test in the timesheet-overnight case)."""

from datetime import date

from timesheet import Shift, group_by_week, load_shifts, minutes_between, parse_time


def test_minutes_between_across_midnight():
    assert minutes_between(22 * 60, 6 * 60) == 480
    assert minutes_between(22 * 60, 0) == 120          # ends exactly at midnight
    assert minutes_between(600, 600) == 24 * 60         # the same time is a full day later
    assert minutes_between(0, 1439) == 1439


def test_an_overnight_shift_with_a_break():
    s = Shift(date(2026, 3, 6), parse_time("10:00pm"), parse_time("6:30am"), 30)
    assert s.minutes == 480


def test_loading_an_overnight_row():
    [s] = load_shifts(["date,start,end\n", "2026-03-06,23:00,7:00\n"])
    assert s.minutes == 480


def test_an_overnight_shift_stays_in_its_starting_week():
    saturday_night = Shift(date(2026, 3, 7), parse_time("11:00pm"), parse_time("7:00am"))
    assert list(group_by_week([saturday_night])) == [date(2026, 3, 1)]
