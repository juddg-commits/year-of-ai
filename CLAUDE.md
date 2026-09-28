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
- **Test on real data, not just the tests you wrote.** The quote matcher passed its first tests and then failed on real Wikipedia quotes (link targets, `[7]` markers). A simulator only tests what it types: the coach's lift history passed every simulated run and read nothing from the real log, which writes "3x8 @135" where the simulator wrote "135x8x3".
- **A paid run must never crash after the money is spent.** Code that touches the network or third-party text catches every error and degrades: a fetch that fails keeps what it had. (A malformed URL raised `httpx.InvalidURL`, which isn't an `httpx.HTTPError`, and would have crashed a run from inside a thread pool.)
- **A cancelled or stuck job gets killed.** Subprocess cleanup goes in `finally`, not only in the timeout branch.
- **Anything that spends money has a ceiling and a human in the loop.** The fleet server caps settings; paid tools aren't pre-approved in permissions.
- **In an MCP stdio server, never print to stdout.** It carries the protocol.
- **LLM judges vary.** A verdict can change when nothing about the item changed (batch context). Don't trust one run's small differences; repeat runs.
- **Give the model the data; don't make it remember or compute.** In the coach's 10-day simulation, the wrong answers traced to data it never saw (weight log, past meals, the workout card on screen) and to dates without weekdays ("three weeks" for 8 days). Compute trends in code, label every date with its weekday and age, and put what the user sees into the prompt.
- **Never show a model a text version of its own tool calls.** The coach's saved logs read "[log_workout] Logged…"; reading them back, it typed the marker instead of calling the tool and told the user "Logged" with nothing saved (1 of 5 simulated runs). Keep tool calls out of transcripts you feed back in.
- **Cache the prompt, frozen part first.** Nothing that changes goes in the cached part, not even today's date. Order the system prompt from frozen to volatile and check `cache_read_input_tokens`. On the coach, this plus the extra context cut the same 10-day run from $2.07 to $1.34.
- **Commit on day one.** Ship #1 sat uncommitted for a month.

## Public repo: keep private things out
This repo is public on GitHub. Never commit `.env`, `runs/`, data folders, or anything about Judd's jobs, internships, school work or personal life. Local-only folders (`brain/`, `outreach/`, `content/`, `.claude/`…) are gitignored for that reason. Before a push, check the diff for secrets and personal details.

## Review loop
After each build: run the tests, review the diff for real bugs (not style), fold any lesson into this file, and let Judd explain the build back in his own words.
