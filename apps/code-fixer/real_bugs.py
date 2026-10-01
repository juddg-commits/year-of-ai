"""Turn a merged bug fix on GitHub into an eval case: the project at the fix, with the fix undone.
Free: GitHub reads (gh) and sandbox runs, no API calls.

  python real_bugs.py add tinydb-633 --repo msiemens/tinydb --pr 633 --keep tinydb,tests,LICENSE \\
      --category "stale state" --split dev

The source is fetched at the merge commit and pinned in evals/sources.json. The fix's own tests
stay and fail: they are the agent's bug report. When more than one test file fails, all but the
one with the fewest failures are hidden, as for planted bugs, so a partial fix fails the grade."""

import argparse
import json
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

from code_fixer import cases, config, realbugs, sandbox, workspace


def gh(*args: str) -> str:
    return subprocess.run(["gh", *args], capture_output=True, check=True, timeout=120).stdout.decode("utf-8")


def choose_hidden(case: cases.Case) -> tuple:
    """Run every test with the bug in; hide the failing test files except the weakest signal."""
    with tempfile.TemporaryDirectory(prefix="code-fixer-real-") as tmp:
        full = sandbox.run_tests(cases.materialize(case, Path(tmp) / "full", hidden=True), case.command)
    failing = [t for t, o in full.outcomes.items() if o in ("failed", "error")]
    by_file = Counter(t.split("::")[0] for t in failing)
    if len(by_file) < 2:
        return [], full
    keep = min(by_file, key=lambda f: (by_file[f], f))
    return sorted(f for f in by_file if f != keep and Path(f).name.startswith("test_") and workspace.is_protected(f)), full


def cmd_add(args) -> int:
    path = cases.CASES_DIR / f"{args.id}.json"
    if path.exists():
        print(f"{args.id} is already a case.")
        return 1
    pr = json.loads(gh("pr", "view", str(args.pr), "-R", args.repo, "--json", "state,mergeCommit,mergedAt,title,files"))
    if pr["state"] != "MERGED":
        print(f"{args.repo}#{args.pr} isn't merged.")
        return 1
    sha = pr["mergeCommit"]["oid"]
    parent = gh("api", f"repos/{args.repo}/commits/{sha}", "--jq", ".parents[0].sha").strip()
    source = f"{args.repo.split('/')[1]}@{sha[:10]}"
    spec = {"repo": args.repo, "commit": sha, "keep": args.keep, "drop": args.drop,
            "files": {"pyproject.toml": "[tool.pytest.ini_options]\n" + "".join(line + "\n" for line in args.pytest)}}
    print(f"Fetching {args.repo} at {sha[:10]} (merged {pr['mergedAt'][:10]}): {pr['title']}")
    root = realbugs.fetch(source, spec, cases.SOURCES_DIR)

    bug = []
    for rel in [f["path"] for f in pr["files"] if f["path"].endswith(".py") and not workspace.is_protected(f["path"])]:
        if not (root / rel).exists():
            print(f"The fix changed {rel}, which --keep leaves out.")
            return 1
        before = gh("api", f"repos/{args.repo}/contents/{rel}?ref={parent}", "-H", "Accept: application/vnd.github.raw")
        bug += realbugs.reverse_edits(rel, (root / rel).read_bytes().decode("utf-8"), before)
    if not bug:
        print("The fix changed no source file outside the tests.")
        return 1

    case = cases.Case(id=args.id, source=source, split=args.split, kind="real", category=args.category,
                      note=f"{args.repo}#{args.pr}, merged {pr['mergedAt'][:10]}: {pr['title']}." + (f" {args.note}" if args.note else ""),
                      bug=bug, test_command=args.test_command)
    case.hidden, full = choose_hidden(case)
    print(f"With the fix undone: {full.summary()}; hidden: {case.hidden or 'none'}")
    verdict = cases.verify(case)
    for problem in verdict["problems"]:
        print(f"   {problem}")
    if not verdict["ok"]:
        print("Not added.")
        return 1
    specs = realbugs.manifest()
    specs[source] = spec
    realbugs.MANIFEST.write_text(json.dumps(dict(sorted(specs.items())), indent=2) + "\n")
    path.write_text(json.dumps(cases.as_dict(case), indent=2) + "\n")
    print(f"Added {path.relative_to(config.ROOT)} ({args.split}): visible {verdict['visible']}, "
          f"hidden tests failing {len(verdict['hidden_failing'])}, {len(bug)} edit(s).")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Turn a merged GitHub bug fix into an eval case (free: no API calls).")
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("add")
    a.add_argument("id")
    a.add_argument("--repo", required=True, help="owner/name")
    a.add_argument("--pr", required=True, type=int)
    a.add_argument("--keep", required=True, type=lambda v: [x for x in v.split(",") if x],
                   help="paths to keep: the package, its tests, the LICENSE (never a changelog)")
    a.add_argument("--drop", default=[], type=lambda v: [x for x in v.split(",") if x], help="paths inside --keep to leave out")
    a.add_argument("--pytest", action="append", default=[], help='a line for [tool.pytest.ini_options], e.g. pythonpath = ["src"]')
    a.add_argument("--test-command", default=config.DEFAULT_TEST_COMMAND)
    a.add_argument("--category", required=True)
    a.add_argument("--note", default="")
    a.add_argument("--split", choices=["dev", "test"], required=True)
    args = p.parse_args()
    return cmd_add(args)


if __name__ == "__main__":
    sys.exit(main())
