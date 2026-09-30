#!/usr/bin/env python3
"""The code fixer's eval: planted and hand-written bugs, graded with tests the agent never sees.

    .venv/bin/python eval.py verify                    # free: checks every case in the sandbox
    .venv/bin/python eval.py run --split dev           # PAID: prints the most it can spend, then stops
    .venv/bin/python eval.py run --split dev --yes     # ...and this spends it

A case is solved when the fix passes the agent's own final check (every visible test) AND the
hidden tests, run on the broken repo with only the agent's changed files on top. Results go to
runs/eval-<time>/: one trace (and patch) per case, and results.json, written after every case.

API failures that survive the SDK's retries are counted apart, never as the agent failing: the
summary leaves them out of the rate and prints the command that reruns them."""

import argparse
import json
import os
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

from code_fixer import agent, cases, config, llm, report, sandbox


def cmd_verify(args) -> int:
    chosen = cases.load(args.cases, args.split)
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
           "effort": args.effort, "solved": False, "api_failure": False}
    with tempfile.TemporaryDirectory(prefix="code-fixer-eval-") as tmp:
        repo = cases.materialize(c, Path(tmp) / c.source)   # the agent sees a repo named after its source
        res = agent.fix(repo, c.command, limits=agent.Limits(max_usd=args.max_usd), model=args.model,
                        effort=args.effort, log=lambda msg: print("   " + msg.strip()))
    graded = cases.grade(c, res.changed) if res.changed else None
    hidden_failing = [t for t, o in graded.outcomes.items() if o in ("failed", "error") and c.is_hidden(t)] if graded else []
    res.trace["eval"] = {"case": cases.as_dict(c), "graded": graded.as_dict() if graded else None}
    paths = report.save(res, out_dir, name=c.id)
    row.update({
        "solved": bool(res.fixed and graded is not None and graded.passed),
        "visible_pass": res.fixed,
        "hidden_pass": graded.passed if graded else False,
        "hidden_failing": hidden_failing,
        "api_failure": res.stop == "api_error",
        "stop": res.stop, "error": res.error, "cost": round(res.cost, 4), "seconds": round(res.seconds, 1),
        "turns": res.turns, "tool_calls": res.tool_calls, "test_runs": res.test_runs,
        "refused_edits": len(res.trace.get("refused_edits", [])), "files_changed": sorted(res.changed),
        "trace": paths["trace"],
    })
    return row


def summarize(rows: list) -> dict:
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
    }


def cmd_run(args) -> int:
    chosen = cases.load(args.cases, args.split)
    if not chosen:
        print("No cases match.")
        return 1
    ceiling = len(chosen) * args.max_usd
    print(f"{len(chosen)} case(s) on {args.model} (effort {args.effort}), at most ${args.max_usd:.2f} each: "
          f"at most ${ceiling:.2f} in total.")
    if not args.yes:
        print("Nothing was run. Add --yes to spend it.")
        return 1
    llm.load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("No ANTHROPIC_API_KEY: copy .env.example to .env and add your key.")
        return 1
    if not sandbox.image_id():
        print(f"The sandbox isn't ready: open OrbStack, then docker build -t {config.IMAGE} sandbox")
        return 1
    print("A long paid run needs the Mac plugged in with the lid open.\n")
    out_dir = config.RUNS_DIR / f"eval-{time.strftime('%Y%m%d-%H%M%S')}"
    out_dir.mkdir(parents=True)
    rows = []
    for i, c in enumerate(chosen, 1):
        print(f"[{i}/{len(chosen)}] {c.id}")
        try:
            row = run_case(c, args, out_dir)
        except Exception as e:   # one broken case must not end a paid run; its money is already in its trace
            row = {"id": c.id, "solved": False, "api_failure": False, "stop": "crash", "error": f"{type(e).__name__}: {e}"}
        rows.append(row)
        print(f"   {'SOLVED' if row['solved'] else 'not solved'} · ${row.get('cost', 0):.3f} · "
              f"{row.get('seconds', 0):.0f}s · {row.get('stop')}" + (" · API failure, not graded" if row["api_failure"] else ""))
        (out_dir / "results.json").write_text(json.dumps({"args": vars(args), "summary": summarize(rows), "cases": rows}, indent=2))
    s = summarize(rows)
    print(f"\nSolved {s['solved']} of {s['graded']} graded case(s)" + (f" ({s['solved_rate']:.0%})" if s["graded"] else "")
          + f" · ${s['cost_total']:.2f} total, ${s['cost_per_case']:.3f} per case · {s['seconds_per_case']:.0f}s per case")
    if s["visible_only"]:
        print(f"Passed the visible tests but failed hidden ones: {', '.join(s['visible_only'])}")
    if s["api_failures"]:
        print(f"Not graded (API failures): rerun with --cases {','.join(s['api_failures'])}")
    print(f"Results: {out_dir / 'results.json'}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Verify or run the code fixer's eval cases.")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("verify", "run"):
        sp = sub.add_parser(name)
        sp.add_argument("--split", choices=["dev", "test"], help="only this split")
        sp.add_argument("--cases", type=lambda s: [x for x in s.split(",") if x], help="comma-separated case ids")
    run = sub.choices["run"]
    run.add_argument("--model", default=config.MODEL)
    run.add_argument("--effort", default=config.EFFORT, choices=["low", "medium", "high", "xhigh", "max"])
    run.add_argument("--max-usd", type=float, default=config.MAX_USD, help="ceiling per case")
    run.add_argument("--yes", action="store_true", help="actually run (it costs money)")
    args = p.parse_args()
    return cmd_verify(args) if args.command == "verify" else cmd_run(args)


if __name__ == "__main__":
    sys.exit(main())
