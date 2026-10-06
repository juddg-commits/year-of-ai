# Code fixer: failing tests in, checked patch out

Point it at a small Python repo whose tests fail. It copies the repo, runs the tests in a locked-down container, lets Claude find and fix the bug with five tools, and checks the result in a fresh container. It never edits your repo: you get a patch to review and a trace of every step.

Project #3 of my Year of AI. Python, Claude API (Opus 5.5), Docker (OrbStack).

**On the held-out set it fixed 9 of 10 bugs, at $0.078 and 43 seconds a fix.** That's a small sample: 10 cases, run once, so the exact 95% interval runs from 55% to 99.7%, and nothing else ran on the same ten to compare against. The score is also a re-grade, not a re-run. When it ran, it scored 10 of 10 by the tests the cases had. Afterward I added hidden tests for the real bugs, and one patch fails them: lark-1641 fixes the method the failing test calls and leaves the cause in another file. Since then I've hand-checked the six real-bug patches against upstream's fixes on random inputs. Five behave the same in every trial, and lark-1641 doesn't. And it only handles small Python repos with pytest tests.

The runs were runs/eval-20261002-131217 (9 cases) and runs/eval-20261002-155904 (tomlkit-550, which I reran on its own after a stalled API call ended the first run before that case was graded). Both used the code at commit b5f812f, and runs/regrade-20261005-104714 re-graded them. The agent didn't run again for that. Its code and the saved patches are unchanged, and only the grader changed, by adding the hidden tests. The stall fix that came afterward only changes each API call's timeout; what the agent does is the same. All three folders are in [sample-runs/](sample-runs/), so you can check the score yourself. The baselines (no test loop, Sonnet 5.5), the failure taxonomy and both hand-checks are in [DESIGN.md](DESIGN.md).

## How it works

1. **Copy.** The repo is copied twice into a temp folder: a pristine copy and a workspace. Virtualenvs, caches, symlinks and anything that looks like a secret (`.env`, keys) are never copied, so they never reach the model.
2. **Confirm the failure.** The tests run in the sandbox first. If they already pass, or the command collects no tests, it stops before any API call.
3. **Fix loop.** Claude gets the failing output and the file list, then works with five tools: `list_files`, `read_file`, `search`, `edit_file` (exact-string replace) and `run_tests`. There is no tool to create or delete files.
4. **Guards in code, not trust in the model.**
   - Test files and pytest's config are read-only: an edit to them is refused and logged.
   - Before every call, its output limit is cut to what the remaining budget can pay for, so the dollar ceiling holds (default $0.50 a fix). Tool calls, test runs, turns and time are capped too, and each API call's timeout is the time the fix has left.
   - "Fixed" is decided by a fresh sandbox run with the test files restored from the pristine copy, never by the model's word. Every test that ran before must pass after; a test that disappears counts as unresolved.
   - The conversation is append-only: Opus 5.5 ties its thinking to the exact history, and the prompt cache needs it too.
5. **Output.** A patch that `git apply` or `patch -p1` applies, the before/after test results, the cost, and a JSON trace of every call, tool result and guard event (failed runs too).

## The sandbox

Every test run is a fresh container: no network, 512 MB of memory, one CPU, 256 processes, no Linux capabilities, a read-only root filesystem, and the workspace mounted read-only. The tests run on a scratch copy inside the container, so nothing they write comes back. A run past 60 seconds is killed, and so is one whose caller is interrupted: the container is removed in `finally`, because stopping the docker client alone would leave it running.

## The eval

29 cases, each verified for free before it counts (`eval.py verify`): without the bug every test passes, visible and hidden, and with it at least one visible test fails.

| | planted | real | hand-written | total |
|---|---|---|---|---|
| dev (tune on it) | 11 | 6 | 2 | 19 |
| test (held out, run once) | 4 | 6 | 0 | 10 |

- **Real** bugs are fixes merged on GitHub after the model's training data, undone; the fix's own tests are the bug report.
- **Planted** bugs are one-token slips generated in well-tested MIT code and screened in the sandbox.
- A case counts as solved only when the fix passes the visible tests and the hidden ones, applied to the broken repo with only the agent's changed files on top. For 10 of the 12 real cases, the hidden tests are ones I wrote from upstream's fix (`evals/hidden/`), since the fix's own tests are the bug report.

### Checking the held-out patches by hand

I compared the six real-bug patches from the held-out run with upstream's fixes on seeded random inputs (run handcheck-heldout-20261005). The probes and the runner are in [evals/handcheck/](evals/handcheck/), so you can rerun it for free. Each probe runs on the bug too, as a control, since a probe that can't tell the bug from upstream proves nothing.

| case | trials | bug differs from upstream | patch differs from upstream |
|---|---|---|---|
| boltons-424 | 400 | 177 | 0 |
| lark-1641 | 240 | 212 | 80 |
| more-itertools-1248 | 600 | 513 | 0 |
| more-itertools-1261 | 800 | 54 | 0 |
| networkx-8734 | 1,841 | 68 | 0 |
| tomlkit-550 | 500 | 270 | 0 |

Five of the patches never differ. All 80 of lark-1641's differences come from copying a parse by copying its parser state alone: the copy and the original still share one lexer, so whichever finishes second fails or comes back with the wrong tree. Copies made through `InteractiveParser.copy()`, the method the patch fixed, never differ. networkx-8734 is the closer call. Upstream changed two functions and the patch changed only one, yet it matched upstream on all 1,841 graphs, including the 3 where upstream's first change, without its second, comes out different. That's evidence on these graphs, not proof.

Sources and licenses: [evals/README.md](evals/README.md). Results by setup: [DESIGN.md](DESIGN.md).

## Run it

Needs Python 3.11+, OrbStack (or Docker) and an Anthropic API key.

```bash
cd apps/code-fixer
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                                # add your ANTHROPIC_API_KEY
docker build -t code-fixer-sandbox:1 sandbox        # once; the build needs the network, test runs never do

.venv/bin/python fix.py path/to/repo                # default test command: python -m pytest -q
.venv/bin/python fix.py path/to/repo --test "python -m pytest -q tests/unit" --max-usd 0.30
.venv/bin/python fix.py path/to/repo --json         # the fleet's result contract
```

Tests and the eval:

```bash
.venv/bin/python -m unittest discover tests         # free: a scripted model and a fake sandbox (+ real containers if OrbStack is up)
.venv/bin/python eval.py verify                     # free: checks every eval case in the sandbox
.venv/bin/python eval.py run --split dev            # paid: prints the most it can spend; add --yes to run
.venv/bin/python eval.py run --split dev --max-total 2.50 --yes   # with a ceiling for the whole run
.venv/bin/python eval.py run --split dev --no-test-loop --yes     # baseline: no run_tests tool
.venv/bin/python eval.py regrade                    # free: saved patches against today's hidden tests
.venv/bin/python evals/handcheck/handcheck.py tinydb-633 runs/eval-<time>   # free: a patch against upstream's fix
.venv/bin/python evals/handcheck/heldout.py         # free: the six held-out patches against upstream's fixes
```

Paid evals run in CI too. I start them by hand ([eval.yml](../../.github/workflows/eval.yml)), and the paid job waits in a `paid-evals` environment until I approve it. That environment is the only place the API key lives, so no push or pull request can spend money. The first one was [run 37398190672](https://github.com/juddg-commits/year-of-ai/actions/runs/37398190672).

## Known limits

- Python repos with pytest-compatible tests only, and only what the sandbox image holds (Python 3.12 and pytest). A repo whose tests need other packages fails in the sandbox.
- Small repos: at most 3,000 files and 30 MB are copied.
- The planted bugs sit in popular libraries the model may have seen in training. The hand-written cases are there partly for that reason.
- A fix can't add a file, and can't change a test even when the test is what's wrong.

## Threat model

I judge the risk with Simon Willison's [lethal trifecta](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/): an agent is open to prompt injection when it has private data, reads untrusted content, and has a way to send data out. The code fixer has the first two. It doesn't have the third.

The private data is whatever repo you point it at, since its files go to the model through Anthropic's API. It never copies files that look like secrets (`.env`, keys, `.netrc`, `.pypirc`), and the API key stays in the fixer's own process, outside the sandbox. The untrusted content is the repo itself. The model reads its code, comments and test output, and any of that can carry instructions. The tests are untrusted code, too. What it lacks is a way out of its own. Its five tools only read, search and edit files in the copy and run the tests, and the tests run with no network. What leaves is a patch you review and a trace on your machine.

The limits live in code. Every test runs in the sandbox: no network, 512 MB, one CPU, 256 processes, no Linux capabilities, a read-only root and workspace, and 60 seconds a run. Test files and pytest's config are read-only, and no tool can create or delete a file. Tool results are capped at 400 lines a read, 100 search matches and 2,000 characters a line, and test output gets cut to its first 2,000 and last 8,000 characters. A fix gets $0.50 by default, enforced before every call and again by the fleet server, plus 40 tool calls, 8 test runs, 30 turns and 15 minutes. Whether the tests pass is decided by a fresh sandbox run that the model has no say in.

How often an injection would get through is not measured. None of the eval cases plants instructions in a repo, so all I can tell you is what one could reach. That includes anything written into the patch that the tests don't catch, so read a patch before you apply it.

## Files

| File | What it does |
|---|---|
| `fix.py` | CLI: one repo in, patch and trace out (`--json` for the fleet) |
| `eval.py` | Verify the cases (free), run the eval (paid, needs `--yes`), or re-grade saved patches (free) |
| `code_fixer/agent.py` | The loop, the limits, the final check |
| `code_fixer/tools.py` | The five tools and their guards |
| `code_fixer/sandbox.py` | Test runs in a fresh container, result parsing |
| `code_fixer/workspace.py` | What gets copied, which files are read-only, the diff |
| `code_fixer/llm.py` | The API call, the cost ledger, the budget cap |
| `code_fixer/cases.py` | Eval cases: plant the bug, hide tests, verify, grade |
| `code_fixer/config.py` | Every knob: the model, the ceilings, the sandbox, the prices |
| `code_fixer/report.py`, `machine.py` | What a run leaves behind; whether the Mac could sleep mid-run |
| `mutants.py`, `code_fixer/mutate.py` | Generate and screen planted bugs (free) |
| `real_bugs.py`, `code_fixer/realbugs.py` | Turn a merged GitHub fix into a case (free) |
| `evals/handcheck/` | Compare a passing patch with upstream's fix on random inputs (free) |
| `DESIGN.md` | How it works and why, with every number and its run |
| `WRITEUP.md` | The short version, for people who don't read code |
| `sandbox/` | The container image: Python 3.12 and pytest |
| `evals/` | The cases and their source projects |
