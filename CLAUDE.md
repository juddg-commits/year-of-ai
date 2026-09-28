# CLAUDE.md

How to work in this repo. Read it before changing anything. When a review catches a mistake, add the lesson here, so the same mistake doesn't happen twice.

## What this is
The Year of AI: one repo, one shipped project at a time (`curriculum/README.md`, `curriculum/log.md`). Each app in `apps/` is standalone: its own venv, `requirements.txt`, README, offline tests and write-up. Agents in the fleet are also reached by the Mother orchestrator through `apps/fleet-mcp/`.

## Conventions
- **Every fleet agent follows the result contract.** Its CLI takes `--json`: progress on stderr, exactly one JSON object on stdout, `{agent, status: "ok"|"error", output, error, cost_usd, seconds, artifacts}`. Adding an agent to the fleet means: a tool function in `apps/fleet-mcp/server.py`, its tool name in Mother's `tools:` line (MCP tools must be listed by name; wildcards don't work there), and a block in `brain/registry.md`.
- **Offline tests for every deterministic step**, with fakes instead of API calls, so they're free: `.venv/bin/python -m unittest discover tests` in each app.
- **Every run leaves a trace**: what it did, what each API call cost, and why it stopped. Failed runs too.
- **A DESIGN.md explains the why, with measured numbers.** Known issues are listed honestly.

## Rules learned the hard way
- **Measure before building.** Count the causes before choosing a fix. The research agent's planned fix for "partial" verdicts targeted the wrong cause (76-88% were cut-off quotes).
- **Only claim numbers that were measured**, in docs, write-ups and posts. Say which run or replay produced them.
- **Test on real data, not just the tests you wrote.** The quote matcher passed its first tests and then failed on real Wikipedia quotes (link targets, `[7]` markers).
- **A paid run must never crash after the money is spent.** Code that touches the network or third-party text catches every error and degrades: a fetch that fails keeps what it had. (A malformed URL raised `httpx.InvalidURL`, which isn't an `httpx.HTTPError`, and would have crashed a run from inside a thread pool.)
- **A cancelled or stuck job gets killed.** Subprocess cleanup goes in `finally`, not only in the timeout branch.
- **Anything that spends money has a ceiling and a human in the loop.** The fleet server caps settings; paid tools aren't pre-approved in permissions.
- **In an MCP stdio server, never print to stdout.** It carries the protocol.
- **LLM judges vary.** A verdict can change when nothing about the item changed (batch context). Don't trust one run's small differences; repeat runs.
- **Commit on day one.** Ship #1 sat uncommitted for a month.

## Public repo: keep private things out
This repo is public on GitHub. Never commit `.env`, `runs/`, data folders, or anything about Judd's jobs, internships, school work or personal life. Local-only folders (`brain/`, `outreach/`, `content/`, `.claude/`…) are gitignored for that reason. Before a push, check the diff for secrets and personal details.

## Review loop
After each build: run the tests, review the diff for real bugs (not style), fold any lesson into this file, and let Judd explain the build back in his own words.
