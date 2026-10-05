# Code fixer: failing tests in, checked patch out

Point it at a small Python repo whose tests fail. It copies the repo, runs the tests in a locked-down container, lets Claude find and fix the bug with five tools, and checks the result in a fresh container. It never edits your repo: you get a patch to review and a trace of every step.

Project #3 of my Year of AI. Python, Claude API (Opus 5.5), Docker (OrbStack).

**Held-out result: 9 of 10**, run once at the end after all tuning, then re-graded with hidden tests for every real case (runs/regrade-20261005-104714). Re-graded, not re-run: the agent didn't run again, and its code and the saved patches are unchanged. Only the grader changed, by adding hidden tests. By the tests the cases had when it ran, it was 10 of 10: 6 of the 10 were real bugs graded only by tests the agent could read. The hidden tests written for them afterward fail one of those patches, lark-1641, which fixes the method the failing test calls and leaves the cause in another file.

The runs: runs/eval-20261002-131217 (9 cases) and runs/eval-20261002-155904 (tomlkit-550, rerun alone after a stalled API call ended the first run before that case was graded). Both ran on the code at commit b5f812f. The stall fix that came after changes only each API call's timeout, not what the agent does. $0.078 and 43 s per fix. None of the held-out patches has been hand-checked against upstream's fix. Baselines (no test loop, Sonnet 5.5), the failure taxonomy and that hand-check on the dev set are in [DESIGN.md](DESIGN.md).

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
```

## Known limits

- Python repos with pytest-compatible tests only, and only what the sandbox image holds (Python 3.12 and pytest). A repo whose tests need other packages fails in the sandbox.
- Small repos: at most 3,000 files and 30 MB are copied.
- The planted bugs sit in popular libraries the model may have seen in training. The hand-written cases are there partly for that reason.
- A fix can't add a file, and can't change a test even when the test is what's wrong.

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
