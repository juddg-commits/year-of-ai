import pytest

from timesheet import format_time, minutes_between, parse_time


def test_24_hour_times():
    assert parse_time("9:30") == 570
    assert parse_time("21:30") == 1290
    assert parse_time("0:00") == 0
    assert parse_time("23:59") == 1439


def test_12_hour_times():
    assert parse_time("9:30pm") == 1290
    assert parse_time("9:30 p.m.") == 1290
    assert parse_time("12:00am") == 0
    assert parse_time("12:15pm") == 735
    assert parse_time("7:05 AM") == 425


@pytest.mark.parametrize("text", ["24:00", "9:60", "13:00pm", "0:30am", "noon", "9:30p", ""])
def test_bad_times(text):
    with pytest.raises(ValueError):
        parse_time(text)


def test_format_time():
    assert format_time(570) == "09:30"
    assert format_time(24 * 60 + 60) == "01:00"


def test_minutes_between_on_the_same_day():
    assert minutes_between(540, 1020) == 480
    assert minutes_between(0, 1) == 1
