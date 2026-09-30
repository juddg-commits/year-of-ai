# timesheet

Turns a timesheet CSV into weekly pay stubs.

```python
from timesheet import load_shifts, pay_stubs

with open("march.csv") as f:
    shifts = load_shifts(f)
for stub in pay_stubs(shifts, hourly_rate="21.50"):
    print(stub.week_of, stub.regular_hours, stub.overtime_hours, stub.gross)
```

The CSV has a header row and the columns `date` (YYYY-MM-DD), `start`, `end` and an optional
`break` in minutes. Times can be 24-hour (`21:30`) or 12-hour (`9:30pm`, `9:30 p.m.`).

## Rules

- A shift that ends at or before its start time ran past midnight. It counts toward the day,
  and the pay week, in which it started.
- Pay weeks start on Sunday unless you pass another `starts_on` day.
- Hours past 40 in a pay week are overtime, paid at 1.5 times the hourly rate.
- Gross pay is rounded once per week, to the cent, half up.
