"""Planted-bug candidates from a source project: generate mutants, screen them in the sandbox,
and add the chosen ones as eval cases. Free: sandbox runs only, no API calls.

  python mutants.py scan --source cachetools --files src/cachetools/__init__.py,src/cachetools/_cached.py
  python mutants.py pick runs/mutants-cachetools-<time>.json --count 10
  python mutants.py add runs/mutants-cachetools-<time>.json --ids <id>,<id> --split dev

A scan runs every mutant against the source's full test suite. For a mutant the tests catch, the
failing test files are hidden except the one with the fewest failures, which stays visible: the
agent gets a weak signal, like a real bug report, and the hidden files check that its fix is
whole. A second run records what the agent would see."""

import argparse
import json
import random
import sys
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from code_fixer import cases, config, mutate, sandbox, workspace



def mutant_id(source: str, m: mutate.Mutant) -> str:
    module = Path(m.file).stem.strip("_")
    return f"{source}-{m.line}-{m.kind}" if module == "init" else f"{source}-{module}-{m.line}-{m.kind}"


def taken_lines(source: str) -> set:
    """(file, line) pairs an existing case already plants a bug on."""
    taken = set()
    for c in cases.load():
        if c.source != source:
            continue
        for edit in c.bug:
            text = (cases.SOURCES_DIR / source / edit["file"]).read_text()
            first = text[:text.find(edit["find"])].count("\n") + 1
            taken.update((edit["file"], first + i) for i in range(edit["find"].count("\n") + 1))
    return taken


def screen(source: str, m: mutate.Mutant, mid: str, command: list, timeout: float) -> dict:
    row = {"id": mid, **m.as_dict()}
    case = cases.Case(id=mid, source=source, split="dev", kind="planted", category=m.category, note="", bug=[m.edit])
    with tempfile.TemporaryDirectory(prefix="code-fixer-mutant-") as tmp:
        full = sandbox.run_tests(cases.materialize(case, Path(tmp) / "full", hidden=True), command, timeout=timeout)
        row["full"] = full.summary()
        failing = [t for t, o in full.outcomes.items() if o in ("failed", "error")]
        if full.error or full.timed_out or not failing:
            row["status"] = "error" if full.error else "hang" if full.timed_out else "survived" if full.passed else "broken"
            return row
        by_file = Counter(t.split("::")[0] for t in failing)
        keep = min(by_file, key=lambda f: (by_file[f], f))
        hidden = sorted(f for f in by_file if f != keep and Path(f).name.startswith("test_") and workspace.is_protected(f))
        case.hidden = hidden
        visible = sandbox.run_tests(cases.materialize(case, Path(tmp) / "visible"), command, timeout=timeout)
    shown = [t for t, o in visible.outcomes.items() if o in ("failed", "error")]
    failures = [line for line in visible.output.splitlines() if line.startswith(("FAILED ", "ERROR "))]
    row.update(status="caught", hidden=hidden, failing_by_file=dict(by_file), visible=visible.summary(),
               visible_failing=len(shown), visible_failing_tests=shown[:5],
               hidden_failing=sum(by_file[f] for f in hidden),
               errors=mutate.errors_in(visible.output),
               failures=[line[:200] for line in failures[:3]],
               shows_file=m.file in visible.output, shows_line=f"{m.file}:{m.line}:" in visible.output)
    return row


def cmd_scan(args) -> int:
    source_dir = cases.SOURCES_DIR / args.source
    command = cases.Case(id="x", source=args.source, split="dev", kind="planted", category="", note="", bug=[]).command
    with tempfile.TemporaryDirectory(prefix="code-fixer-mutant-") as tmp:
        dest = Path(tmp) / "reference"
        reference = sandbox.run_tests(cases.materialize(
            cases.Case(id="ref", source=args.source, split="dev", kind="planted", category="", note="", bug=[]),
            dest, bug=False, hidden=True), command, timeout=args.timeout)
    if not reference.passed:
        print(f"The source's own tests don't pass: {reference.summary()}", file=sys.stderr)
        return 1
    taken = taken_lines(args.source)
    found, ids = [], Counter()
    for rel in args.files:
        for m in mutate.mutants((source_dir / rel).read_text(), rel):
            if (rel, m.line) in taken:
                continue
            mid = mutant_id(args.source, m)
            ids[mid] += 1
            found.append((m, mid if ids[mid] == 1 else f"{mid}-{ids[mid]}"))
    if args.limit and args.limit < len(found):
        found = random.Random(args.seed).sample(found, args.limit)
    print(f"{args.source}: reference {reference.summary()}; screening {len(found)} mutants "
          f"with {args.workers} workers…", file=sys.stderr)
    start, rows = time.monotonic(), []
    with ThreadPoolExecutor(args.workers) as pool:
        futures = [pool.submit(screen, args.source, m, mid, command, args.timeout) for m, mid in found]
        for i, future in enumerate(futures, 1):
            rows.append(future.result())
            if i % 25 == 0 or i == len(futures):
                print(f"   {i}/{len(futures)} · {Counter(r['status'] for r in rows)}", file=sys.stderr)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    out = config.RUNS_DIR / f"mutants-{args.source}-{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"source": args.source, "files": args.files, "reference": reference.summary(),
                               "seconds": round(time.monotonic() - start), "mutants": rows}, indent=1))
    by_kind = {}
    for r in rows:
        by_kind.setdefault(r["kind"], Counter())[r["status"]] += 1
    print(f"\n{len(rows)} mutants in {time.monotonic() - start:.0f}s: {dict(Counter(r['status'] for r in rows))}")
    for kind, counts in sorted(by_kind.items()):
        print(f"   {kind:9} {dict(counts)}")
    print(f"Report: {out}")
    return 0


def cmd_pick(args) -> int:
    rows = json.loads(Path(args.report).read_text())["mutants"]
    chosen = mutate.pick(rows, args.count, args.seed)
    print(f"{len(mutate.eligible(rows))} eligible of {len(rows)}; {len(chosen)} picked (one per function, seed {args.seed}):")
    for r in chosen:
        print(f"\n{r['id']}  [{r['category']}]  {r['function'] or '-'}  visible failing {r['visible_failing']}, "
              f"hidden failing {r['hidden_failing']}, line shown: {r['shows_line']}")
        print(f"   - {r['before']}\n   + {r['after'] or '(deleted)'}")
    print("\nids: " + ",".join(r["id"] for r in chosen))
    return 0


def cmd_add(args) -> int:
    report = json.loads(Path(args.report).read_text())
    rows = {r["id"]: r for r in report["mutants"]}
    for mid in args.ids:
        r = rows[mid]
        what = f"`{r['before']}` became `{r['after']}`" if r["after"] else f"`{r['before']}` was deleted"
        case = cases.Case(id=mid, source=report["source"], split=args.split, kind="planted", category=r["category"],
                          note=f"Generated mutant ({r['kind']}) in {r['function'] or 'module level'}, {r['file']} "
                               f"line {r['line']}: {what}. Visible: {r['visible']}; hidden failing: {r['hidden_failing']}.",
                          bug=[{"file": r["file"], "find": r["find"], "replace": r["replace"]}], hidden=r["hidden"])
        path = cases.CASES_DIR / f"{mid}.json"
        if path.exists():
            print(f"{mid}: already a case, skipped", file=sys.stderr)
            continue
        path.write_text(json.dumps(cases.as_dict(case), indent=2) + "\n")
        print(f"added {path.relative_to(config.ROOT)} ({args.split})")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Generate, screen and add planted-bug eval cases (free: no API calls).")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--source", required=True)
    s.add_argument("--files", required=True, type=lambda v: [x for x in v.split(",") if x])
    s.add_argument("--workers", type=int, default=3)
    s.add_argument("--timeout", type=float, default=30, help="seconds per test run; a mutant that hangs is dropped")
    s.add_argument("--limit", type=int, help="screen a seeded sample of this many")
    s.add_argument("--seed", type=int, default=0)
    k = sub.add_parser("pick")
    k.add_argument("report")
    k.add_argument("--count", type=int, default=10)
    k.add_argument("--seed", type=int, default=0)
    a = sub.add_parser("add")
    a.add_argument("report")
    a.add_argument("--ids", required=True, type=lambda v: [x for x in v.split(",") if x])
    a.add_argument("--split", choices=["dev", "test"], required=True)
    args = p.parse_args()
    return {"scan": cmd_scan, "pick": cmd_pick, "add": cmd_add}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
