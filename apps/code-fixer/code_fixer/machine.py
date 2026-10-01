"""Whether this Mac can be trusted to stay awake through a paid run."""

import re
import subprocess
import sys


MIN_BATTERY = 30   # percent: a short run on a charged battery is fine; macOS sleeps a nearly empty one


def power_problem_from(battery: str, lid: str) -> str | None:
    if "Battery Power" in battery:
        m = re.search(r"(\d+)%", battery)
        charge = int(m[1]) if m else 0
        if charge < MIN_BATTERY:
            return f"the battery is at {charge}%: plug the Mac in"
    if re.search(r'"AppleClamshellState" = Yes', lid):
        return "the lid is closed, and macOS sleeps with it closed unless an external display is attached"
    return None


def power_problem() -> str | None:
    """Why this Mac could sleep mid-run, or None: a closed lid, or a low battery when unplugged.
    Paid runs have been cut short three times by exactly these (in-flight calls lost).
    macOS only; elsewhere it can't tell."""
    if sys.platform != "darwin":
        return None
    try:
        battery = subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=10).stdout
        lid = subprocess.run(["ioreg", "-r", "-k", "AppleClamshellState", "-d", "4"],
                             capture_output=True, text=True, timeout=10).stdout
    except (subprocess.TimeoutExpired, OSError):
        return None
    return power_problem_from(battery, lid)
