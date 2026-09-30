#!/usr/bin/env python3
"""Code fixer: a Python repo with failing tests in, a checked patch out.

    .venv/bin/python fix.py path/to/repo
    .venv/bin/python fix.py path/to/repo --test "python -m pytest -q tests/unit" --max-usd 0.30

It copies the repo, runs its tests in a sandbox (they must fail), lets Claude find and fix the
bug with five tools, then checks the result in a fresh sandbox run. The repo itself is never
touched: you get a patch (runs/*.patch) to review and apply, and a trace of the run (runs/*.json).

With --json it follows the fleet's result contract instead: progress on stderr, and stdout gets
exactly one JSON object: {agent, status: "ok"|"error", output, error, cost_usd, seconds, artifacts}.
"ok" means the fix passed the final check."""

import argparse
import json
import os
import shlex
import sys

from code_fixer import agent, config, llm, report, sandbox


def contract(status: str, *, output=None, error=None, cost=0.0, seconds=0.0, artifacts=None) -> str:
    return json.dumps({"agent": "code-fixer", "status": status, "output": output, "error": error,
                       "cost_usd": round(cost, 4), "seconds": round(seconds, 1), "artifacts": artifacts or {}})


def main() -> int:
    p = argparse.ArgumentParser(description="Failing tests in, a checked patch out.")
    p.add_argument("repo", help="path to the repo (it is copied, never modified)")
    p.add_argument("--test", default=config.DEFAULT_TEST_COMMAND,
                   help=f'the command that runs its tests (default "{config.DEFAULT_TEST_COMMAND}")')
    p.add_argument("--max-usd", type=float, default=config.MAX_USD,
                   help=f"dollar ceiling for this fix (default {config.MAX_USD:.2f})")
    p.add_argument("--model", default=config.MODEL, help=f"default {config.MODEL}")
    p.add_argument("--effort", default=config.EFFORT, choices=["low", "medium", "high", "xhigh", "max"],
                   help=f"default {config.EFFORT}")
    p.add_argument("--json", action="store_true",
                   help="print one JSON result on stdout, progress on stderr (the fleet's result contract)")
    args = p.parse_args()
    say = (lambda msg="": print(msg, file=sys.stderr)) if args.json else print

    def fail(message: str, **extra) -> int:
        print(contract("error", error=message, **extra) if args.json else message,
              file=sys.stdout if args.json else sys.stderr)
        return 1

    llm.load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return fail("No ANTHROPIC_API_KEY: copy .env.example to .env and add your key.")
    if not sandbox.docker_bin():
        return fail("Docker isn't available: install and open OrbStack (see README).")
    if not sandbox.image_id():
        return fail(f"The sandbox image isn't built: docker build -t {config.IMAGE} sandbox")
    if args.max_usd <= 0 or args.max_usd > 5:
        return fail("--max-usd must be above 0 and at most 5")
    try:
        command = shlex.split(args.test)
    except ValueError as e:
        return fail(f"Can't parse --test: {e}")

    limits = agent.Limits(max_usd=args.max_usd)
    try:
        res = agent.fix(args.repo, command, limits=limits, model=args.model, effort=args.effort, log=say)
    except (ValueError, OSError) as e:   # bad path, repo too big: raised before any API call
        return fail(f"Can't start: {e}")
    paths = report.save(res)
    if args.json:
        if res.fixed:
            print(contract("ok", output=report.markdown(res), cost=res.cost, seconds=res.seconds, artifacts=paths))
            return 0
        return fail(report.headline(res), cost=res.cost, seconds=res.seconds, artifacts=paths)
    print("\n" + report.markdown(res))
    print(f"\nTrace: {paths['trace']}" + (f"\nPatch: {paths['patch']}" if "patch" in paths else ""))
    return 0 if res.fixed else 1


if __name__ == "__main__":
    sys.exit(main())
