#!/usr/bin/env python3
"""Research agent: ask a hard question, get a cited brief.

    .venv/bin/python research.py "How are US hospitals using AI scribes, and does it cut burnout?"

Prints progress per stage, the brief, and what each stage cost. Saves the brief
(runs/*.md) and a full JSON trace of the run (runs/*.json)."""

import argparse
import os
import sys

from research_agent import agent, config, report
from research_agent.llm import AgentError


def main() -> int:
    p = argparse.ArgumentParser(description="Question in, cited brief out.")
    p.add_argument("question", nargs="+", help="what to research")
    p.add_argument("--sub-questions", type=int, default=config.MAX_SUB_QUESTIONS,
                   help=f"max sub-questions (default {config.MAX_SUB_QUESTIONS})")
    p.add_argument("--searches", type=int, default=config.SEARCHES_PER_WORKER,
                   help=f"max web searches per worker (default {config.SEARCHES_PER_WORKER})")
    args = p.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("No ANTHROPIC_API_KEY: copy .env.example to .env and add your key.", file=sys.stderr)
        return 1

    question = " ".join(args.question)
    try:
        run = agent.run(question, max_sub_questions=args.sub_questions, searches=args.searches)
    except AgentError as e:
        print(f"\nStopped: {e}", file=sys.stderr)
        if getattr(e, "run", None):
            path = report.save_failed(e.run, str(e))
            print(f"Spent ${e.run.ledger.total():.2f}. Partial trace: {path.relative_to(config.ROOT)}", file=sys.stderr)
        return 1

    md, js = report.save(run)
    print("\n" + "─" * 72 + "\n")
    print(report.brief_markdown(run))
    print("\n" + report.cost_table(run))
    if run.audit.uncited:
        print(f"\n⚠ {len(run.audit.uncited)} sentence(s) without a citation (see trace → audit.uncited)")
    print(f"\nSaved: {md.relative_to(config.ROOT)}  ·  trace: {js.relative_to(config.ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
