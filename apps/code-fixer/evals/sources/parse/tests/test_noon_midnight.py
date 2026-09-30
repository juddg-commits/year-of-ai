"""Not upstream: added for the code-fixer eval (a hidden test in one case).
12-hour clock times at noon and midnight, through the time-only and date-time formats."""

from datetime import datetime, time

import parse


def test_noon_and_midnight_time_only():
    assert parse.parse("{:tt}", "12:00:00 PM")[0] == time(12, 0, 0)
    assert parse.parse("{:tt}", "12:30:15 PM")[0] == time(12, 30, 15)
    assert parse.parse("{:tt}", "12:30:15 AM")[0] == time(0, 30, 15)
    assert parse.parse("{:tt}", "1:05:00 PM")[0] == time(13, 5, 0)
    assert parse.parse("{:tt}", "11:59:59 AM")[0] == time(11, 59, 59)


def test_noon_in_a_date():
    assert parse.parse("{:tg}", "1/2/2011 12:00 PM")[0] == datetime(2011, 2, 1, 12, 0)
    assert parse.parse("{:tg}", "1/2/2011 12:00 AM")[0] == datetime(2011, 2, 1, 0, 0)
