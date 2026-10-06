#!/usr/bin/env python3
"""Hand-check of the six held-out real cases: does each patch from the held-out run behave like
upstream's merged fix, beyond the tests?

    .venv/bin/python evals/handcheck/heldout.py [case ...]

Same method as handcheck.py, with one probe per case (probe_<case>.py here). Each probe runs on
upstream's fix, the bug (the control) and the bug with the agent's held-out patch, read from
sample-runs/, and we count the trials whose results differ from upstream. The numbers in README.md
and DESIGN.md are from run handcheck-heldout-20261005; a rerun saves runs/handcheck-heldout-<time>/.

Free: no API calls. The first run downloads the cases' sources from GitHub, at their pinned commits."""

import json
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP = HERE.parents[1]
sys.path.insert(0, str(APP))
from code_fixer import cases, config  # noqa: E402

PATCH_RUNS = {"tomlkit-550": APP / "sample-runs/eval-20261002-155904"}
DEFAULT_RUN = APP / "sample-runs/eval-20261002-131217"
CASES = ["boltons-424", "lark-1641", "more-itertools-1248", "more-itertools-1261", "networkx-8734", "tomlkit-550"]


def run_probe(probe: Path, copy: Path) -> list:
    out = subprocess.run([sys.executable, str(probe)], cwd=copy, env={"PYTHONPATH": str(copy), "PATH": "/usr/bin:/bin"},
                         capture_output=True, text=True, timeout=1800)
    if out.returncode:
        raise RuntimeError(f"{probe.name} failed on {copy.name}: {out.stderr[-800:]}")
    return json.loads(out.stdout)


def main() -> int:
    chosen = sys.argv[1:] or CASES
    unknown = [c for c in chosen if c not in CASES]
    if unknown:
        print(f"Not a held-out real case: {', '.join(unknown)}. This checks {', '.join(CASES)}; handcheck.py takes any case.")
        return 1
    try:   # a fresh clone has none of the real bugs' sources (they're git-ignored)
        cases.ensure_sources(cases.load(chosen))
    except (OSError, ValueError, tarfile.TarError) as e:   # network, GitHub, a bad tarball, as in eval.py
        print(f"Couldn't fetch a source: {type(e).__name__}: {e}")
        return 1
    summary = {}
    for case_id in chosen:
        case = cases.load([case_id])[0]
        probe = HERE / f"probe_{case_id.replace('-', '_')}.py"
        run = PATCH_RUNS.get(case_id, DEFAULT_RUN)
        with tempfile.TemporaryDirectory(prefix="handcheck-") as tmp:
            tmp = Path(tmp)
            copies = {"upstream": cases.materialize(case, tmp / "upstream", bug=False, hidden=True),
                      "bug (control)": cases.materialize(case, tmp / "bug", hidden=True)}
            dest = cases.materialize(case, tmp / "patch", hidden=True)
            applied = subprocess.run(["patch", "-p1", "-s", "-i", str(run / f"{case_id}.patch")], cwd=dest,
                                     capture_output=True, text=True)
            if applied.returncode:
                print(f"{case_id}: the patch doesn't apply: {applied.stdout}{applied.stderr}")
                return 1
            copies[f"patch ({run.name})"] = dest
            results = {name: run_probe(probe, copy) for name, copy in copies.items()}
        upstream = results["upstream"]
        print(f"{case_id}: {len(upstream)} trials of {probe.name}")
        summary[case_id] = {"trials": len(upstream)}
        for name, got in results.items():
            if name == "upstream":
                continue
            differ = [i for i, (a, b) in enumerate(zip(upstream, got)) if a != b]
            summary[case_id][name] = {"differ": len(differ), "first": differ[:10],
                                      "examples": [{"trial": i, "upstream": upstream[i], "got": got[i]} for i in differ[:3]]}
            print(f"  {name:32} differs from upstream in {len(differ):>4} trials" + (f" (first: {differ[:8]})" if differ else ""))
    out_dir = config.RUNS_DIR / f"handcheck-heldout-{time.strftime('%Y%m%d-%H%M%S')}"
    out_dir.mkdir(parents=True)
    (out_dir / "results.json").write_text(json.dumps(summary, indent=1, default=str))
    print(f"Saved {out_dir.relative_to(APP)}/results.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
