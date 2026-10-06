#!/usr/bin/env python3
"""Hand-check: does a passing patch behave like upstream's merged fix, beyond the tests?

    .venv/bin/python evals/handcheck/handcheck.py tinydb-633 runs/eval-20261001-163752 runs/eval-20261001-165125

For a real-bug case it builds three kinds of copy: upstream's fix (the source as merged), the
bug (the fix undone, as the agent got it) and the bug with each run's patch applied. Then it
runs the case's probe (probe_<case>.py here, or probe_<project>.py if the case has none: seeded
random operations, one JSON list of results per trial) against each copy and counts the trials
whose results differ from upstream.
The bug is the control: a probe that can't tell it from upstream proves nothing.

Free: no API calls. The probes run locally, outside the sandbox, on the pinned sources."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from code_fixer import cases  # noqa: E402

HERE = Path(__file__).resolve().parent


def probe_for(case: cases.Case) -> Path:
    own = HERE / f"probe_{case.id.replace('-', '_')}.py"
    if own.exists():   # a project's probe may be for another of its cases (probe_more_itertools.py is 1285's)
        return own
    project = case.source.split("@")[0].replace("-", "_")
    return HERE / f"probe_{project}.py"


def run_probe(probe: Path, copy: Path) -> list:
    out = subprocess.run([sys.executable, str(probe)], cwd=copy, env={"PYTHONPATH": str(copy), "PATH": "/usr/bin:/bin"},
                         capture_output=True, text=True, timeout=600)
    if out.returncode:
        raise RuntimeError(f"{probe.name} failed on {copy.name}: {out.stderr[-500:]}")
    return json.loads(out.stdout)


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 1
    case = cases.load([sys.argv[1]])[0]
    probe = probe_for(case)
    if not probe.exists():
        print(f"No probe for {case.id}: write {probe.name}")
        return 1
    with tempfile.TemporaryDirectory(prefix="handcheck-") as tmp:
        tmp = Path(tmp)
        copies = {"upstream": cases.materialize(case, tmp / "upstream", bug=False, hidden=True),
                  "bug (control)": cases.materialize(case, tmp / "bug", hidden=True)}
        for run in sys.argv[2:]:
            patch = (Path(run) / f"{case.id}.patch").resolve()
            dest = cases.materialize(case, tmp / Path(run).name, hidden=True)
            applied = subprocess.run(["patch", "-p1", "-s", "-i", str(patch)], cwd=dest, capture_output=True, text=True)
            if applied.returncode:
                print(f"{patch} doesn't apply: {applied.stdout}{applied.stderr}")
                return 1
            copies[Path(run).name] = dest
        results = {name: run_probe(probe, copy) for name, copy in copies.items()}
    upstream = results["upstream"]
    print(f"{case.id}: {len(upstream)} trials of {probe.name}")
    for name, got in results.items():
        if name == "upstream":
            continue
        differ = [i for i, (a, b) in enumerate(zip(upstream, got)) if a != b]
        print(f"  {name:28} differs from upstream in {len(differ):>3} trials" + (f" (first: {differ[:8]})" if differ else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
