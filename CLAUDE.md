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
- **Test on real data, not just the tests you wrote.** The quote matcher passed its first tests and then failed on real Wikipedia quotes (link targets, `[7]` markers). A simulator only tests what it types: the coach's lift history passed every simulated run and read nothing from the real log, which writes "3x8 @140" where the simulator wrote "140x8x3".
- **A paid run must never crash after the money is spent.** Code that touches the network or third-party text catches every error and degrades: a fetch that fails keeps what it had. (A malformed URL raised `httpx.InvalidURL`, which isn't an `httpx.HTTPError`, and would have crashed a run from inside a thread pool.)
- **A cancelled or stuck job gets killed.** Subprocess cleanup goes in `finally`, not only in the timeout branch.
- **A time limit checked between calls doesn't bound a call.** The code fixer checked its 15-minute limit once per turn. On the held-out run, one API call stalled past 30 minutes (the SDK allows 10 per attempt, with retries) until the session's 40-minute background limit killed the whole eval, losing that case's trace and cost. Each call's timeout is now the time left, split across its attempts. Give a long paid run the longest background limit the harness allows.
- **Anything that spends money has a ceiling and a human in the loop.** The fleet server caps settings; paid tools aren't pre-approved in permissions.
- **In an MCP stdio server, never print to stdout.** It carries the protocol.
- **LLM judges vary.** A verdict can change when nothing about the item changed (batch context). Don't trust one run's small differences; repeat runs.
- **Give the model the data; don't make it remember or compute.** In the coach's 10-day simulation, the wrong answers traced to data it never saw (weight log, past meals, the workout card on screen) and to dates without weekdays ("three weeks" for 8 days). Compute trends in code, label every date with its weekday and age, and put what the user sees into the prompt.
- **Never show a model a text version of its own tool calls.** The coach's saved logs read "[log_workout] Logged…"; reading them back, it typed the marker instead of calling the tool and told the user "Logged" with nothing saved (1 of 5 simulated runs). Keep tool calls out of transcripts you feed back in.
- **Score a failure everywhere it can happen, not just where you first saw it.** The coach's nag check looked only at the knee, so three runs scored 12/12 while the coach asked for bodyweight in 4-5 replies each. An empty context section reads as a blocker to the model: say "nothing yet, and nothing waits on it."
- **An error handler that hides the error hides the bug.** The coach's chat caught every exception and said "Hit a snag", so a run that lost all of day 1 to rejected requests left no reason why. Log the type, status and message of every failed call.
- **Check in code that a claimed action happened.** A coach reply said "Logged: 3 slices pepperoni" with no tool call, and nothing was saved (1 of 5 runs on the final code). The chat loop now catches "Logged" with no tool in the turn and asks once for the call.
- **Cache the prompt, frozen part first.** Nothing that changes goes in the cached part, not even today's date. Order the system prompt from frozen to volatile and check `cache_read_input_tokens`. On the coach, this plus the extra context cut the same 10-day run from $2.07 to $1.34.
- **With sparse traffic, the cache write is the cost.** On the site's assistant, the first question after five quiet minutes cost about $0.14 on Opus 5, and the ones right after it about $0.02.
- **Keep network failures out of the score.** Count them apart, retry them, and never grade them as the bot's failures. A 60-second timeout turned API stalls into fake failures; patient timeouts plus a second pass over dropped cases fixed it.
- **A long paid run on a laptop needs power and an open lid.** `caffeinate -i` doesn't stop lid-close or low-battery sleep. Two eval runs were cut short in one day, their in-flight calls lost as connection errors.
- **Read a script as text; never import it to inspect it.** Its top-level code runs: importing an eval to count its cases started the paid eval.
- **Before paying for a bigger model, check whether the prompt causes the problem.** The assistant's stiff hiring answers came from its prompt's template, which a stronger model follows just the same. Re-tune after a switch: Opus 5's hiring answers ran about 150 words until the prompt capped them (median answer 53 words after, with a length check in the eval).
- **When a model keeps breaking a phrasing rule inside lists, change what it volunteers.** A credit line kept attaching to projects it didn't belong to until project lists stopped volunteering it; a code guard catches the rest.
- **Check a README's claims against its own data before repeating them.** A data project's README headlined a finding its own data contradicted.
- **Save a measurement's output in a run folder when you take it.** The research agent's 64% → 41% replay printed only to the screen, and the code fixer's stall log sat in a temp folder; neither could back a post. Rerun and saved, the replay gave 66% → 54%.
- **Measure a layout where it will be used.** A page that fit on screen overflowed when printed: measure a printout at the printed width.
- **Run the paid path against a fake client before the first real call.** A scripted-model test found the code fixer's cost ledger reading `.type` off the API's top-level usage object, which has none: the first paid call would have crashed the run after spending.
- **Verify an eval case before it counts.** The code fixer runs each case without the bug (every test must pass) and with it (a visible test must fail) before using it. That first check caught two broken tests in the hand-written cases.
- **Read what the model was shown, even when it succeeds.** The code fixer solved all 5 dev cases while up to 95% of the test output it read was pytest's PASSED lines, which pushed failure tracebacks out of the trimmed tail (30,122 characters cut from a 26-failure run). It now reads a count instead; grading still parses the full output.
- **Check what a commit leaves out, not just what it adds.** An unanchored `sandbox/` in the root .gitignore, meant for one local folder, silently dropped the code fixer's Dockerfile; `git add -n` showed it missing.
- **Commit on day one.** Ship #1 sat uncommitted for weeks.
- **One chat per working tree.** A second chat opened while the first was still building: the handoff was hours stale, and a test failed on the other chat's container. Before editing, check `git status` and for another running session.
- **A handoff is a state file, not a log.** Rewrite `brain/state.md` at the end of each session and move what's history to `brain/history/`. Appended to session after session, one handoff reached 85 KB with five "START HERE" sections, and the older ones contradicted the newer ones.

## Public repo: keep private things out
This repo is public on GitHub. Never commit `.env`, `runs/`, data folders, or anything about Judd's jobs, internships, school work or personal life. Local-only folders (`brain/`, `outreach/`, `content/`, `.claude/`…) are gitignored for that reason. Before a push, check the diff for secrets and personal details. Test files are public too: an eval's never-say terms spell out what they protect, so they live in a git-ignored list.

## Review loop
After each build: run the tests, review the diff for real bugs (not style), fold any lesson into this file, and let Judd explain the build back in his own words. At the end of every session, rewrite `brain/state.md`; a new session starts there.
