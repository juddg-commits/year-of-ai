#!/usr/bin/env python3
"""Research agent: ask a hard question, get a cited brief.

    .venv/bin/python research.py "How are US hospitals using AI scribes, and does it cut burnout?"

Prints progress per stage, the brief, and what each stage cost. Saves the brief
(runs/*.md) and a full JSON trace of the run (runs/*.json).

With --json it follows the fleet's result contract instead, so the fleet MCP server
(and Mother) can call it: progress goes to stderr, and stdout gets exactly one JSON
object: {agent, status: "ok"|"error", output, error, cost_usd, seconds, artifacts}."""

import argparse
import json
import os
import sys

from research_agent import agent, config, report
from research_agent.llm import AgentError


def contract(status: str, *, output=None, error=None, cost=0.0, seconds=0.0, artifacts=None) -> str:
    return json.dumps({"agent": "research-agent", "status": status, "output": output, "error": error,
                       "cost_usd": round(cost, 4), "seconds": round(seconds, 1), "artifacts": artifacts or {}})


def main() -> int:
    p = argparse.ArgumentParser(description="Question in, cited brief out.")
    p.add_argument("question", nargs="+", help="what to research")
    p.add_argument("--sub-questions", type=int, default=config.MAX_SUB_QUESTIONS,
                   help=f"max sub-questions (default {config.MAX_SUB_QUESTIONS})")
    p.add_argument("--searches", type=int, default=config.SEARCHES_PER_WORKER,
                   help=f"max web searches per worker (default {config.SEARCHES_PER_WORKER})")
    p.add_argument("--json", action="store_true",
                   help="print one JSON result on stdout, progress on stderr (the fleet's result contract)")
    args = p.parse_args()
    say = (lambda msg="": print(msg, file=sys.stderr)) if args.json else print

    def fail(message: str, **extra) -> int:
        print(contract("error", error=message, **extra) if args.json else message,
              file=sys.stdout if args.json else sys.stderr)
        return 1

    if not os.environ.get("ANTHROPIC_API_KEY"):
        return fail("No ANTHROPIC_API_KEY: copy .env.example to .env and add your key.")

    question = " ".join(args.question)
    try:
        run = agent.run(question, max_sub_questions=args.sub_questions, searches=args.searches, log=say)
    except AgentError as e:
        if not getattr(e, "run", None):
            return fail(f"Stopped: {e}")
        path = report.save_failed(e.run, str(e))
        say(f"Spent ${e.run.ledger.total():.2f}. Partial trace: {path.relative_to(config.ROOT)}")
        return fail(f"Stopped: {e}", cost=e.run.ledger.total(), artifacts={"trace": str(path)})

    md, js = report.save(run)
    if args.json:
        print(contract("ok", output=report.brief_markdown(run), cost=run.ledger.total(),
                       seconds=sum(run.timings.values()), artifacts={"brief": str(md), "trace": str(js)}))
        return 0
    print("\n" + "─" * 72 + "\n")
    print(report.brief_markdown(run))
    print("\n" + report.cost_table(run))
    if run.audit.uncited:
        print(f"\n⚠ {len(run.audit.uncited)} sentence(s) without a citation (see trace → audit.uncited)")
    print(f"\nSaved: {md.relative_to(config.ROOT)}  ·  trace: {js.relative_to(config.ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
