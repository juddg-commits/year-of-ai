#!/usr/bin/env python3
"""The code fixer's eval: planted, hand-written and real bugs, graded with tests the agent never sees.

    .venv/bin/python eval.py fetch                     # free: downloads the real bugs' sources (pinned commits)
    .venv/bin/python eval.py verify                    # free: checks every case in the sandbox
    .venv/bin/python eval.py run --split dev           # PAID: prints the most it can spend, then stops
    .venv/bin/python eval.py run --split dev --yes     # ...and this spends it
    .venv/bin/python eval.py run --split dev --no-test-loop --max-total 2.50 --yes   # the baseline without test runs
    .venv/bin/python eval.py regrade runs/eval-<time> ...   # free: grades saved patches against today's hidden tests

A case is solved when the fix passes the agent's own final check (every visible test) AND the
hidden tests, run on the broken repo with only the agent's changed files on top. Results go to
runs/eval-<time>/: one trace (and patch) per case, and results.json, written after every case.

--max-total is a ceiling for the whole run: a case isn't started if its own ceiling could take the
run past it. The cases left out are listed as not run, never as failures.

API failures that survive the SDK's retries are counted apart, never as the agent failing: the
summary leaves them out of the rate and prints the command that reruns them. One that no retry
fixes (400, 401, 403, 404: an empty credit balance, a bad key) stops the run: every case after it
would fail the same way."""

import argparse
import json
import os
import sys
import tarfile
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from code_fixer import agent, cases, config, llm, machine, report, sandbox

FATAL_STATUSES = {400, 401, 403, 404}   # the SDK doesn't retry these, and the next case won't fix them


def fetch_sources(chosen: list) -> bool:
    try:
        cases.ensure_sources(chosen)
        return True
    except (OSError, ValueError, tarfile.TarError) as e:   # network, GitHub, a bad tarball: nothing spent yet
        print(f"Couldn't fetch a source: {type(e).__name__}: {e}")
        return False


def cmd_fetch(args) -> int:
    return 0 if fetch_sources(cases.load(args.cases, args.split)) else 1


def cmd_verify(args) -> int:
    chosen = cases.load(args.cases, args.split)
    if not fetch_sources(chosen):
        return 1
    bad = 0
    for c in chosen:
        v = cases.verify(c)
        bad += not v["ok"]
        print(f"{'ok ' if v['ok'] else 'BAD'} {c.id:28} visible: {v['visible']:32} hidden tests failing: {len(v['hidden_failing'])}")
        for problem in v["problems"]:
            print(f"      {problem}")
    print(f"\n{len(chosen) - bad} of {len(chosen)} cases verified.")
    return 1 if bad else 0


def run_case(c: cases.Case, args, out_dir: Path) -> dict:
    row = {"id": c.id, "split": c.split, "kind": c.kind, "category": c.category, "model": args.model,
           "effort": args.effort, "test_loop": not args.no_test_loop, "solved": False, "api_failure": False}
    with tempfile.TemporaryDirectory(prefix="code-fixer-eval-") as tmp:
        repo = cases.materialize(c, Path(tmp) / c.source.split("@")[0])   # named after its project, no commit
        res = agent.fix(repo, c.command, limits=agent.Limits(max_usd=args.max_usd), model=args.model,
                        effort=args.effort, log=lambda msg: print("   " + msg.strip()),
                        test_loop=not args.no_test_loop)
    graded = cases.grade(c, res.changed) if res.changed else None
    hidden_failing = [t for t, o in graded.outcomes.items() if o in ("failed", "error") and c.is_hidden(t)] if graded else []
    res.trace["eval"] = {"case": cases.as_dict(c), "graded": graded.as_dict() if graded else None}
    paths = report.save(res, out_dir, name=c.id)
    row.update({
        "solved": bool(res.fixed and graded is not None and graded.passed),
        "visible_pass": res.fixed,
        "hidden_pass": graded.passed if graded else False,
        "hidden_failing": hidden_failing,
        "api_failure": res.stop == "api_error", "api_status": (res.trace.get("api_error") or {}).get("status"),
        "stop": res.stop, "error": res.error, "cost": round(res.cost, 4), "seconds": round(res.seconds, 1),
        "turns": res.turns, "tool_calls": res.tool_calls, "test_runs": res.test_runs,
        "refused_edits": len(res.trace.get("refused_edits", [])), "files_changed": sorted(res.changed),
        "trace": paths["trace"],
    })
    return row


def summarize(rows: list, not_run: list | None = None) -> dict:
    graded = [r for r in rows if not r["api_failure"]]
    solved = [r for r in graded if r["solved"]]
    cost = sum(r.get("cost", 0) for r in rows)
    return {
        "cases": len(rows), "graded": len(graded), "solved": len(solved),
        "solved_rate": round(len(solved) / len(graded), 3) if graded else None,
        "visible_only": [r["id"] for r in graded if r.get("visible_pass") and not r["solved"]],
        "api_failures": [r["id"] for r in rows if r["api_failure"]],
        "cost_total": round(cost, 4), "cost_per_case": round(cost / len(rows), 4) if rows else None,
        "seconds_per_case": round(sum(r.get("seconds", 0) for r in rows) / len(rows), 1) if rows else None,
        "stops": dict(Counter(r.get("stop") for r in rows)),
        "not_run": list(not_run or []),
    }


def cmd_run(args) -> int:
    chosen = cases.load(args.cases, args.split)
    if not chosen:
        print("No cases match.")
        return 1
    ceiling = len(chosen) * args.max_usd
    if args.max_total is not None:
        if args.max_total < args.max_usd:
            print(f"--max-total (${args.max_total:.2f}) is below one case's ceiling (${args.max_usd:.2f}): nothing could run.")
            return 1
        ceiling = min(ceiling, args.max_total)
    print(f"{len(chosen)} case(s) on {args.model} (effort {args.effort}"
          + ("" if not args.no_test_loop else ", no test loop") + f"), at most ${args.max_usd:.2f} each: "
          f"at most ${ceiling:.2f} in total.")
    if not args.yes:
        print("Nothing was run. Add --yes to spend it.")
        return 1
    llm.load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("No ANTHROPIC_API_KEY: copy .env.example to .env and add your key.")
        return 1
    problem = machine.power_problem()
    if problem and not args.ignore_power:
        print(f"Not started: {problem}. (--ignore-power runs it anyway.)")
        return 1
    if not sandbox.image_id():
        print(f"The sandbox isn't answering: open OrbStack (or run orbctl start). If the image is missing: "
              f"docker build -t {config.IMAGE} sandbox")
        return 1
    if not fetch_sources(chosen):
        return 1
    print()
    out_dir = config.RUNS_DIR / f"eval-{time.strftime('%Y%m%d-%H%M%S')}"
    out_dir.mkdir(parents=True)
    rows, not_run = [], []
    for i, c in enumerate(chosen, 1):
        spent = sum(r.get("cost", args.max_usd) for r in rows)   # a crashed case's cost is unknown: count its ceiling
        if args.max_total is not None and spent + args.max_usd > args.max_total:
            not_run = [x.id for x in chosen[i - 1:]]
            print(f"Stopped before {c.id}: ${spent:.2f} spent, and one more case could pass the "
                  f"${args.max_total:.2f} ceiling. Not run: {', '.join(not_run)}")
            break
        print(f"[{i}/{len(chosen)}] {c.id}")
        try:
            row = run_case(c, args, out_dir)
        except Exception as e:   # one broken case must not end a paid run; its money is already in its trace
            row = {"id": c.id, "solved": False, "api_failure": False, "stop": "crash", "error": f"{type(e).__name__}: {e}"}
        rows.append(row)
        print(f"   {'SOLVED' if row['solved'] else 'not solved'} · ${row.get('cost', 0):.3f} · "
              f"{row.get('seconds', 0):.0f}s · {row.get('stop')}" + (" · API failure, not graded" if row["api_failure"] else ""))
        (out_dir / "results.json").write_text(json.dumps({"args": vars(args), "summary": summarize(rows), "cases": rows}, indent=2))
        if row["api_failure"] and row.get("api_status") in FATAL_STATUSES:
            not_run = [x.id for x in chosen[i:]]
            print(f"\nStopped: HTTP {row['api_status']} isn't retried and every case would hit it. {row.get('error', '')[:300]}")
            break
    s = summarize(rows, not_run)
    (out_dir / "results.json").write_text(json.dumps({"args": vars(args), "summary": s, "cases": rows}, indent=2))
    print(f"\nSolved {s['solved']} of {s['graded']} graded case(s)" + (f" ({s['solved_rate']:.0%})" if s["graded"] else "")
          + f" · ${s['cost_total']:.2f} total, ${s['cost_per_case']:.3f} per case · {s['seconds_per_case']:.0f}s per case")
    if s["visible_only"]:
        print(f"Passed the visible tests but failed hidden ones: {', '.join(s['visible_only'])}")
    if s["api_failures"]:
        print(f"Not graded (API failures): rerun with --cases {','.join(s['api_failures'])}")
    if s["not_run"]:
        print(f"Not run (the run's ceiling): --cases {','.join(s['not_run'])}")
    print(f"Results: {out_dir / 'results.json'}")
    return 0


def regrade_row(run_dir: Path, row: dict, case: cases.Case) -> dict:
    """One saved patch against the case as it is now. Solved needs the agent's own final check
    (unchanged, from the run) and today's hidden tests."""
    out = {"run": run_dir.name, "id": row["id"], "solved_before": bool(row.get("solved")), "solved_now": False,
           "visible_pass": bool(row.get("visible_pass")), "hidden_failing": [], "error": None}
    patch = run_dir / f"{row['id']}.patch"
    if not patch.exists():   # the agent changed nothing: unsolved then and now
        return out
    try:
        graded = cases.grade(case, cases.patched_files(case, patch.read_text()))
    except Exception as e:   # one bad patch or container must not end the re-grade
        return {**out, "error": f"{type(e).__name__}: {e}"}
    if graded.error or graded.timed_out:   # the sandbox's failure, not the patch's: rerun it, never score it
        return {**out, "error": graded.summary()}
    out["hidden_failing"] = [t for t, o in graded.outcomes.items() if o in ("failed", "error") and case.is_hidden(t)]
    out["graded"] = graded.summary()
    out["solved_now"] = out["visible_pass"] and graded.passed
    return out


def cmd_regrade(args) -> int:
    run_dirs = [Path(r) for r in args.runs] or sorted(config.RUNS_DIR.glob("eval-*"))
    chosen = {c.id: c for c in cases.load(args.cases, args.split)}
    if not fetch_sources(list(chosen.values())):
        return 1
    if not sandbox.image_id():
        print("The sandbox isn't answering: open OrbStack (or run orbctl start).")
        return 1
    jobs, runs = [], []
    for run_dir in run_dirs:
        try:
            saved = json.loads((run_dir / "results.json").read_text())
        except (OSError, ValueError) as e:
            print(f"Skipped {run_dir}: {type(e).__name__}: {e}")
            continue
        a = saved.get("args", {})
        runs.append({"run": run_dir.name, "split": a.get("split"), "model": a.get("model"),
                     "test_loop": not a.get("no_test_loop")})
        jobs += [(run_dir, r, chosen[r["id"]]) for r in saved.get("cases", [])
                 if r["id"] in chosen and not r.get("api_failure") and r.get("stop") != "crash"]
    print(f"Re-grading {len(jobs)} saved result(s) from {len(runs)} run(s)…")
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(lambda job: regrade_row(*job), jobs))
    for run in runs:
        mine = [r for r in rows if r["run"] == run["run"]]
        run.update({"graded": len(mine), "solved_before": sum(r["solved_before"] for r in mine),
                    "solved_now": sum(r["solved_now"] for r in mine),
                    "now_failing": [r["id"] for r in mine if r["solved_before"] and not r["solved_now"]],
                    "now_passing": [r["id"] for r in mine if r["solved_now"] and not r["solved_before"]],
                    "errors": [r["id"] for r in mine if r["error"]]})
    out_dir = config.RUNS_DIR / f"regrade-{time.strftime('%Y%m%d-%H%M%S')}"
    out_dir.mkdir(parents=True)
    (out_dir / "results.json").write_text(json.dumps(
        {"cases": sorted(chosen), "hidden": {i: c.hidden for i, c in sorted(chosen.items())},
         "runs": runs, "rows": rows}, indent=2))
    for run in runs:
        print(f"{run['run']:22} {run['split'] or 'some':5} {run['model'] or '?':18} {'loop' if run['test_loop'] else 'no loop':8}"
              f" solved {run['solved_before']} → {run['solved_now']} of {run['graded']}"
              + (f" · now failing: {', '.join(run['now_failing'])}" if run["now_failing"] else "")
              + (f" · errors: {', '.join(run['errors'])}" if run["errors"] else ""))
    print(f"Results: {out_dir / 'results.json'}")
    return 1 if any(r["error"] for r in rows) else 0


def main() -> int:
    p = argparse.ArgumentParser(description="Fetch, verify, run or re-grade the code fixer's eval cases.")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("fetch", "verify", "run", "regrade"):
        sp = sub.add_parser(name)
        sp.add_argument("--split", choices=["dev", "test"], help="only this split")
        sp.add_argument("--cases", type=lambda s: [x for x in s.split(",") if x], help="comma-separated case ids")
    run = sub.choices["run"]
    run.add_argument("--model", default=config.MODEL)
    run.add_argument("--effort", default=config.EFFORT, choices=["low", "medium", "high", "xhigh", "max"])
    run.add_argument("--max-usd", type=float, default=config.MAX_USD, help="ceiling per case")
    run.add_argument("--max-total", type=float, help="ceiling for the whole run")
    run.add_argument("--no-test-loop", action="store_true",
                     help="baseline: no run_tests tool, one patch checked once (what the test loop buys)")
    run.add_argument("--yes", action="store_true", help="actually run (it costs money)")
    run.add_argument("--ignore-power", action="store_true", help="run on battery or with the lid closed")
    sub.choices["regrade"].add_argument("runs", nargs="*", help="run folders (default: every runs/eval-*)")
    args = p.parse_args()
    return {"fetch": cmd_fetch, "verify": cmd_verify, "run": cmd_run, "regrade": cmd_regrade}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
