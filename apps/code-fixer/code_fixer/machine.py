"""Whether this Mac can be trusted to stay awake through a paid run."""

import re
import subprocess
import sys


def power_problem_from(battery: str, lid: str) -> str | None:
    if "Battery Power" in battery:
        return "the Mac is on battery: plug it in"
    if re.search(r'"AppleClamshellState" = Yes', lid):
        return "the lid is closed, and macOS sleeps with it closed unless an external display is attached"
    return None


def power_problem() -> str | None:
    """Why this Mac could sleep mid-run, or None. Paid runs have been cut short three times by a
    low battery or a closed lid (in-flight calls lost). macOS only; elsewhere it can't tell."""
    if sys.platform != "darwin":
        return None
    try:
        battery = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=10).stdout
        lid = subprocess.run(["ioreg", "-r", "-k", "AppleClamshellState", "-d", "4"],
                             capture_output=True, text=True, timeout=10).stdout
    except (subprocess.TimeoutExpired, OSError):
        return None
    return power_problem_from(battery, lid)
