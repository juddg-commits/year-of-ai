# Code fixer: failing tests in, checked patch out

Point it at a small Python repo whose tests fail. It copies the repo, runs the tests in a locked-down container, lets Claude find and fix the bug with five tools, and checks the result in a fresh container. It never edits your repo: you get a patch to review and a trace of every step.

Project #3 of my Year of AI. Python, Claude API (Opus 5.5), Docker (OrbStack).

**Status: built and tested offline. The first paid eval run hasn't happened yet; results and a DESIGN.md with measured numbers come after it.**

## How it works

1. **Copy.** The repo is copied twice into a temp folder: a pristine copy and a workspace. Virtualenvs, caches, symlinks and anything that looks like a secret (`.env`, keys) are never copied, so they never reach the model.
2. **Confirm the failure.** The tests run in the sandbox first. If they already pass, or the command collects no tests, it stops before any API call.
3. **Fix loop.** Claude gets the failing output and the file list, then works with five tools: `list_files`, `read_file`, `search`, `edit_file` (exact-string replace) and `run_tests`. There is no tool to create or delete files.
4. **Guards in code, not trust in the model.**
   - Test files and pytest's config are read-only: an edit to them is refused and logged.
   - Before every call, its output limit is cut to what the remaining budget can pay for, so the dollar ceiling holds (default $0.50 a fix). Tool calls, test runs, turns and time are capped too.
   - "Fixed" is decided by a fresh sandbox run with the test files restored from the pristine copy, never by the model's word. Every test that ran before must pass after; a test that disappears counts as unresolved.
   - The conversation is append-only: Opus 5.5 ties its thinking to the exact history, and the prompt cache needs it too.
5. **Output.** A patch that `git apply` or `patch -p1` applies, the before/after test results, the cost, and a JSON trace of every call, tool result and guard event (failed runs too).

## The sandbox

Every test run is a fresh container: no network, 512 MB of memory, one CPU, 256 processes, no Linux capabilities, a read-only root filesystem, and the workspace mounted read-only. The tests run on a scratch copy inside the container, so nothing they write comes back. A run past 60 seconds is killed, and so is one whose caller is interrupted: the container is removed in `finally`, because stopping the docker client alone would leave it running.

## The eval

Bugs planted in real code, graded with tests the agent never sees:

| Case | Source | Bug |
|---|---|---|
| `cachetools-ttl-boundary` | cachetools 7.2.0 (MIT) | Off by one: an item at exactly its TTL isn't expired |
| `cachetools-resize-update` | cachetools 7.2.0 (MIT) | Wrong variable: updating an item counts its full size |
| `parse-noon-pm` | parse 1.22.2 (MIT) | Missed edge case: 12 PM becomes hour 24 |
| `timesheet-overnight` | timesheet (hand-written) | The failing test is in pay; the cause is two modules down |
| `timesheet-week-start` | timesheet (hand-written) | A sign error that only weeks starting on Sunday expose |

Each case is checked before use, for free (`eval.py verify`): without the bug every test passes, visible and hidden; with it, at least one visible test fails. A case counts as solved only when the fix passes the visible tests and the hidden ones, applied to the broken repo with only the agent's changed files on top. Sources and licenses: [evals/README.md](evals/README.md). The plan is about 30 cases, with a dev split for tuning and a held-out split for the reported number.

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
| `eval.py` | Verify the cases (free) or run the eval (paid, needs `--yes`) |
| `code_fixer/agent.py` | The loop, the limits, the final check |
| `code_fixer/tools.py` | The five tools and their guards |
| `code_fixer/sandbox.py` | Test runs in a fresh container, result parsing |
| `code_fixer/workspace.py` | What gets copied, which files are read-only, the diff |
| `code_fixer/llm.py` | The API call, the cost ledger, the budget cap |
| `code_fixer/cases.py` | Eval cases: plant the bug, hide tests, verify, grade |
| `sandbox/` | The container image: Python 3.12 and pytest |
| `evals/` | The cases and their source projects |
