#!/usr/bin/env python3
"""Replay the validation stage on a saved run: API quotes vs quotes recovered from the page.

A controlled comparison. Same claims, same validator prompt, same model; the only
difference between the two arms is whether cut-off quotes were extended from the
source page. Research isn't re-run, so search noise can't blur the result.

    .venv/bin/python revalidate.py runs/<run>.json             # both arms, ~$0.70
    .venv/bin/python revalidate.py --quotes-only runs/<run>.json   # free: just the recovery stats
"""

import argparse
import copy
import json
import os
import sys
from collections import Counter
from dataclasses import fields

from research_agent import pages, validator
from research_agent.evidence import Evidence, Source
from research_agent.llm import Ledger

VERDICTS = ("supported", "partial", "unsupported")


def load(path: str) -> tuple:
    trace = json.load(open(path))
    names = {f.name for f in fields(Evidence)}
    evidence = [Evidence(**{k: v for k, v in e.items() if k in names}) for e in trace["evidence"]]
    for ev in evidence:
        if ev.api_quote:          # a run saved after quote recovery: start again from the API's excerpt
            ev.quote, ev.api_quote = ev.api_quote, ""
        ev.verdict = ev.reason = ""
    return evidence, {k: Source(**v) for k, v in trace["sources"].items()}


def judge(label: str, evidence: list, sources: dict) -> None:
    ledger = Ledger()
    validator.validate(ledger, evidence, sources)
    counts = Counter(ev.verdict for ev in evidence)
    print(f"{label:<16}" + "".join(f"{v} {counts[v]:>3} ({counts[v] / len(evidence):.0%})   " for v in VERDICTS)
          + f"${ledger.total():.3f}")


def main() -> int:
    p = argparse.ArgumentParser(description="Re-run validation on a saved trace, with and without recovered quotes.")
    p.add_argument("trace", help="a runs/*.json file")
    p.add_argument("--quotes-only", action="store_true", help="only recover quotes and print the stats (no API calls)")
    args = p.parse_args()

    api_arm, sources = load(args.trace)
    page_arm = copy.deepcopy(api_arm)
    stats = pages.extend_quotes(page_arm)
    print(f"{len(api_arm)} evidence items · {stats.truncated} cut-off quotes · {stats.extended} extended "
          f"from {stats.pages} pages · not found on page: {stats.not_found} · not fetched: {stats.page_failures or 0}")
    if stats.errors:
        print(f"⚠ extend_quote raised on {len(stats.errors)} quote(s), first: {stats.errors[0]}")
    if args.quotes_only:
        return 0
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("No ANTHROPIC_API_KEY: copy .env.example to .env and add your key.", file=sys.stderr)
        return 1

    print("\nValidating both arms…")
    judge("API quotes", api_arm, sources)
    judge("page quotes", page_arm, sources)

    pairs = list(zip(api_arm, page_arm))
    flips = Counter((a.verdict, b.verdict, a.quote != b.quote) for a, b in pairs if a.verdict != b.verdict)
    kept = sum(a.quote == b.quote for a, b in pairs)
    print(f"\nChanged verdicts (API → page). Items that kept their quote ({kept}) only change by run-to-run noise:")
    for before, after in sorted({(x, y) for x, y, _ in flips}, key=lambda k: -flips[k + (True,)]):
        print(f"  {before:>11} → {after:<11} quote recovered: {flips[before, after, True]:>3}   "
              f"quote unchanged (noise): {flips[before, after, False]:>2}")
    worse = [(a, b) for a, b in pairs
             if a.quote != b.quote and VERDICTS.index(b.verdict) > VERDICTS.index(a.verdict)]
    for a, b in worse[:3]:           # read these: is the validator right, or did we recover the wrong sentence?
        print(f"\n  got worse: {a.verdict} → {b.verdict}\n  CLAIM: {b.claim[:200]}\n  QUOTE: {b.quote[:300]}\n  WHY:   {b.reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
