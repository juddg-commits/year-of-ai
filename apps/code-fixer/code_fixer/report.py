"""What a run leaves behind: a JSON trace for every run (failed ones too), the patch when there
is one, and the result in words."""

import json
import re
import time
from pathlib import Path

from . import config
from .agent import STOPS, FixResult


def save(res: FixResult, runs_dir: Path | None = None, name: str | None = None) -> dict:
    """Writes <stamp>-<repo>.json (and .patch); `name` replaces the stamped name (the eval uses the case id)."""
    runs_dir = Path(runs_dir or config.RUNS_DIR)
    runs_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(res.repo).name)[:40] or "repo"
    base = runs_dir / (name or f"{time.strftime('%Y%m%d-%H%M%S')}-{slug}{'' if res.fixed else '-NOT-FIXED'}")
    trace, patch = Path(f"{base}.json"), Path(f"{base}.patch")   # not with_suffix: repo names can hold dots
    trace.write_text(json.dumps(res.trace, indent=2, default=str))
    paths = {"trace": str(trace)}
    if res.diff:
        patch.write_text(res.diff)
        paths["patch"] = str(patch)
    return paths


def headline(res: FixResult) -> str:
    if res.stop == "nothing_to_fix":
        return f"Nothing to fix: the tests already pass ({res.before.summary()})."
    if res.fixed:
        return f"Fixed. After: {res.after.summary()}. Before: {res.before.summary()}."
    why = STOPS.get(res.stop, res.stop) + (f": {res.error}" if res.error else "")
    after = f" After: {res.after.summary()}." if res.after else ""
    left = f" Still not passing: {', '.join(res.unresolved[:5])}{' …' if len(res.unresolved) > 5 else ''}." if res.unresolved else ""
    return f"Not fixed ({why}).{after}{left}"


def markdown(res: FixResult) -> str:
    parts = [headline(res)]
    if res.summary:
        parts.append(res.summary)
    if res.diff:
        parts.append(f"```diff\n{res.diff}```")
    parts.append(f"${res.cost:.2f} · {res.seconds:.0f}s · {res.turns} turns · {res.tool_calls} tool calls · "
                 f"{res.test_runs} test runs · {res.model}")
    return "\n\n".join(parts)
