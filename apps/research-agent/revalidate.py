#!/usr/bin/env python3
"""Replay the validation stage on a saved run: API quotes vs quotes recovered from the page.

A controlled comparison. Same claims, same validator prompt, same model; the only
difference between the two arms is whether cut-off quotes were extended from the
source page. Research isn't re-run, so search noise can't blur the result.

    .venv/bin/python revalidate.py runs/<run>.json                    # both arms: $0.69 on the AI scribes run, at most $1.50
    .venv/bin/python revalidate.py --max-total 1.00 runs/<run>.json   # a lower ceiling
    .venv/bin/python revalidate.py --quotes-only runs/<run>.json      # free: just the recovery stats

A paid replay saves runs/revalidate-<time>/: results.json (both arms' verdict on every item,
and every call's cost) and summary.txt (what it printed). Saved even when a call fails."""

import argparse
import copy
import json
import os
import sys
import time
from collections import Counter
from dataclasses import asdict, fields
from pathlib import Path

from research_agent import config, pages, validator
from research_agent.evidence import Evidence, Source
from research_agent.llm import Ledger

VERDICTS = ("supported", "partial", "unsupported")
PAGE_ARM_MARGIN = 1.5      # recovered quotes are longer: price the page arm at 1.5x the API arm
FALLBACK_PER_ITEM = 0.005  # per item per arm when the trace has no validate calls (the tuned AI scribes run: $0.003)


def load(path: str) -> tuple:
    trace = json.load(open(path))
    names = {f.name for f in fields(Evidence)}
    evidence = [Evidence(**{k: v for k, v in e.items() if k in names}) for e in trace["evidence"]]
    for ev in evidence:
        if ev.api_quote:          # a run saved after quote recovery: start again from the API's excerpt
            ev.quote, ev.api_quote = ev.api_quote, ""
        ev.verdict = ev.reason = ""
    recorded = sum(c["cost"] for c in trace.get("calls", []) if c.get("stage") == "validate")
    return evidence, {k: Source(**v) for k, v in trace["sources"].items()}, recorded


def judge(ledger: Ledger, evidence: list, sources: dict) -> dict:
    before = ledger.total()
    validator.validate(ledger, evidence, sources)
    counts = Counter(ev.verdict for ev in evidence)
    return {"counts": {v: counts[v] for v in VERDICTS}, "cost": ledger.total() - before}


def save(out: Path, record: dict, api_arm: list, page_arm: list, ledger: Ledger, lines: list) -> None:
    out.mkdir(parents=True, exist_ok=True)
    record["total_cost"] = ledger.total()
    record["calls"] = ledger.as_dicts()
    record["items"] = [{"id": a.id, "claim": a.claim, "api_quote": a.quote, "page_quote": b.quote,
                        "api_verdict": a.verdict, "api_reason": a.reason,
                        "page_verdict": b.verdict, "page_reason": b.reason} for a, b in zip(api_arm, page_arm)]
    (out / "results.json").write_text(json.dumps(record, indent=1, default=str))
    (out / "summary.txt").write_text("\n".join(lines) + "\n")


def main() -> int:
    p = argparse.ArgumentParser(description="Re-run validation on a saved trace, with and without recovered quotes.")
    p.add_argument("trace", help="a runs/*.json file")
    p.add_argument("--quotes-only", action="store_true", help="only recover quotes and print the stats (no API calls)")
    p.add_argument("--max-total", type=float, default=1.50, help="dollar ceiling for both arms together (default 1.50)")
    p.add_argument("--out", help="folder for results.json and summary.txt (default: runs/revalidate-<time>)")
    args = p.parse_args()

    lines = []

    def say(text: str = "") -> None:
        print(text)
        lines.append(text)

    api_arm, sources, recorded = load(args.trace)
    page_arm = copy.deepcopy(api_arm)
    stats = pages.extend_quotes(page_arm)
    say(f"{len(api_arm)} evidence items · {stats.truncated} cut-off quotes · {stats.extended} extended "
        f"from {stats.pages} pages · not found on page: {stats.not_found} · not fetched: {stats.page_failures or 0}")
    if stats.errors:
        say(f"⚠ extend_quote raised on {len(stats.errors)} quote(s), first: {stats.errors[0]}")
    if args.quotes_only:
        return 0
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("No ANTHROPIC_API_KEY: copy .env.example to .env and add your key.", file=sys.stderr)
        return 1

    per_arm = recorded or FALLBACK_PER_ITEM * len(api_arm)
    estimate = per_arm * (1 + PAGE_ARM_MARGIN)
    if estimate > args.max_total:
        print(f"Estimated ${estimate:.2f} is over the ${args.max_total:.2f} ceiling: nothing was run.", file=sys.stderr)
        return 1

    out = Path(args.out) if args.out else config.ROOT / "runs" / time.strftime("revalidate-%Y%m%d-%H%M%S")
    record = {"trace": args.trace, "max_total": args.max_total, "estimate": estimate,
              "quote_stats": asdict(stats), "arms": {}, "error": ""}
    ledger = Ledger()
    say(f"\nValidating both arms (estimate ${estimate:.2f}, ceiling ${args.max_total:.2f})…")
    try:
        for label, items in (("API quotes", api_arm), ("page quotes", page_arm)):
            if record["arms"]:   # before the second arm: the ceiling can't stop an arm halfway, so check here
                next_cost = record["arms"]["API quotes"]["cost"] * PAGE_ARM_MARGIN
                if ledger.total() + next_cost > args.max_total:
                    record["error"] = (f"stopped before '{label}': ${ledger.total():.3f} spent + ~${next_cost:.3f} "
                                       f"would pass the ${args.max_total:.2f} ceiling")
                    say(record["error"])
                    break
            arm = record["arms"][label] = judge(ledger, items, sources)
            say(f"{label:<16}" + "".join(f"{v} {arm['counts'][v]:>3} ({arm['counts'][v] / len(items):.0%})   "
                                         for v in VERDICTS) + f"${arm['cost']:.3f}")

        if len(record["arms"]) == 2:
            pairs = list(zip(api_arm, page_arm))
            flips = Counter((a.verdict, b.verdict, a.quote != b.quote) for a, b in pairs if a.verdict != b.verdict)
            kept = sum(a.quote == b.quote for a, b in pairs)
            say(f"\nChanged verdicts (API → page). Items that kept their quote ({kept}) only change by run-to-run noise:")
            for before, after in sorted({(x, y) for x, y, _ in flips}, key=lambda k: -flips[k + (True,)]):
                say(f"  {before:>11} → {after:<11} quote recovered: {flips[before, after, True]:>3}   "
                    f"quote unchanged (noise): {flips[before, after, False]:>2}")
            worse = [(a, b) for a, b in pairs
                     if a.quote != b.quote and VERDICTS.index(b.verdict) > VERDICTS.index(a.verdict)]
            for a, b in worse[:3]:   # read these: is the validator right, or did we recover the wrong sentence?
                say(f"\n  got worse: {a.verdict} → {b.verdict}\n  CLAIM: {b.claim[:200]}\n"
                    f"  QUOTE: {b.quote[:300]}\n  WHY:   {b.reason}")
    except Exception as e:   # money may already be spent: keep the record instead of crashing
        record["error"] = f"{type(e).__name__}: {e}"
        say(f"⚠ {record['error']}")
    finally:
        say(f"\nTotal ${ledger.total():.3f} · saved to {out}")
        save(out, record, api_arm, page_arm, ledger, lines)
    return 1 if record["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
