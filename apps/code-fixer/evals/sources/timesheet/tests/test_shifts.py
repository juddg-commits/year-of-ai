from datetime import date
from decimal import Decimal

import pytest

from timesheet import Shift, load_shifts


def test_load_shifts():
    lines = ["date,start,end,break\n", "2026-03-02,9:00,5:30pm,30\n", "2026-03-03, 8:00 ,16:00,\n"]
    first, second = load_shifts(lines)
    assert first == Shift(date(2026, 3, 2), 540, 1050, 30)
    assert second == Shift(date(2026, 3, 3), 480, 960, 0)


def test_minutes_and_hours():
    s = Shift(date(2026, 3, 2), 540, 1050, 30)
    assert s.minutes == 480
    assert s.hours == Decimal(8)
    assert Shift(date(2026, 3, 2), 540, 590).hours == Decimal(50) / 60


def test_a_break_longer_than_the_shift_is_an_error():
    with pytest.raises(ValueError, match="break"):
        Shift(date(2026, 3, 2), 540, 560, 30).minutes


def test_a_bad_row_names_its_line():
    lines = ["date,start,end\n", "2026-03-02,9:00,5:00pm\n", "2026-03-03,nine,17:00\n"]
    with pytest.raises(ValueError, match="line 3"):
        load_shifts(lines)
