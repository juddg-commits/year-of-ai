# Design notes: how the code fixer works and why

Written to be explained out loud. Every number here was measured, and each one names the eval run that produced it (`runs/eval-<date>-<time>/`, kept on my machine: traces and patches aren't committed). Unless a row says otherwise: Claude Opus 5.5, effort `medium`, a $0.50 ceiling per fix, measured 2026-09-30 to 2026-10-02.

## The pipeline

```
repo + test command
   │
   ▼
[1] COPY        CODE: a pristine copy and a workspace. No virtualenvs, caches, symlinks
   │            or files that look like secrets (.env, keys): they never reach the model
   ▼
[2] CONFIRM     a sandbox run. Tests already passing, or no tests collected:
   │            stop before any API call
   ▼
[3] FIX LOOP    Opus 5.5 with five tools: list_files, read_file, search, edit_file, run_tests
   │            test files read-only · the budget cut before every call · append-only history
   ▼
[4] CHECK       CODE: copy the workspace, restore the test files from the pristine copy,
   │            run in a fresh sandbox. Every test that ran before must pass after
   ▼
   patch + before/after results + a JSON trace of every call, tool result and guard event

   eval only:
[5] GRADE       the broken case + the hidden tests + only the agent's changed files → sandbox
```

**The model decides what to read and what to change. Code decides whether it worked.** "Fixed" is never the model's word. In 9 of the 10 pyflakes-872 runs the model reported that every test passes (the tenth had no test tool and said so), and the hidden test failed in all 10.

## How I know it works

**The eval.** 29 cases, each verified for free before it counts (`eval.py verify`): without the bug every test passes, visible and hidden, and with it at least one visible test fails. That check caught two broken tests in my own hand-written cases.

| | planted | real | hand-written | total |
|---|---|---|---|---|
| dev (tune on it) | 11 | 6 | 2 | 19 |
| test (held out, run once) | 4 | 6 | 0 | 10 |

- **Real** bugs are fixes merged on GitHub in July to September 2026, after the model's training data, undone. The fix's own tests are the bug report.
- **Planted** bugs are one-token slips (a boundary, a flipped comparison, a dropped `not`, a missing statement) generated in well-tested MIT code and screened in the sandbox. Easy ones are left out: a bug whose traceback points at its own line, or whose error names its cause.
- **Hidden tests**: for a generated planted bug, every failing test file except the one with the fewest failures, so the agent gets a weak signal like a real bug report. The first planted cases and the hand-written ones have hidden tests written for them. 10 of the 12 real cases have none (see Known limits).

**The number: 10 of 10 on the held-out set, by the eval.** Run once, at the end, after all tuning: runs/eval-20261002-131217 (9 cases) and runs/eval-20261002-155904 (tomlkit-550). $0.78 in total, $0.078 and 43 s per case.
- One case was rerun. The first run was killed while tomlkit-550 waited on a stalled API call, before that case was graded (section 7). The rerun used the same code and settings, and nothing was tuned in between.
- Both runs used the code at commit b5f812f. The stall fix came after them, and it changes only each API call's timeout, not what the agent does.
- Two caveats. 6 of the 10 are real bugs with no hidden tests, so they're graded by the full visible suite. And none of the held-out patches has been hand-checked: by the standard below, 10 of 10 is an upper bound.

**Dev**, for comparison: 18 of 19 in every setup that had the test loop (below). The miss is always the same case, pyflakes-872.

## What each design choice bought

Same 19 dev cases, same $0.50 ceiling per case, one run each. Two counts per row:
- **by the eval**: the visible tests and the hidden ones pass.
- **by hand-check**: the same, plus the three real cases checked against upstream's fix on random inputs (the hand-check section below). A patch fails the hand-check if it raises an error or loses data where upstream's fix doesn't. Different but valid ID numbers don't count against it, and neither does a failed insert that names its own ID: under any valid numbering, the ID a caller asks for may already be taken. Only an insert that leaves the ID to tinydb has to succeed. Every row is held to this standard.

| setup | by the eval | by hand-check | total | per case | time per case | run |
|---|---|---|---|---|---|---|
| **Opus 5.5 + test loop** (the default) | 18 of 19 | 18 of 19 | $1.03 | $0.054 | 23 s | eval-20261002-125922 |
| Opus 5.5, **no** `run_tests` tool | 17 of 19 | 17 of 19 | $1.05 | $0.055 | 22 s | eval-20261002-124619 |
| **Sonnet 5.5** + test loop | 18 of 19 | **17 of 19** | $0.58 | $0.030 | 22 s | eval-20261002-124640 |
| Opus 5.5 + test loop + "search elsewhere" line (reverted) | 18 of 19 | 18 of 19 | $1.18 | $0.062 | 28 s | eval-20261001-165125 |

### 1. The test loop
The baseline is the same agent, prompt and limits minus `run_tests`: one patch, checked once after the model stops (`agent.fix(test_loop=False)`; the prompt differs by one line).
- **It bought one case**, more-itertools-1285. Opus's first fix there is right about the cause (a `defaultdict` lookup invents a key) but breaks a different visible test (`BucketTests::test_list`): a key whose items went straight to the caller stops being listed. That first fix was wrong in 3 of 4 Opus attempts. The two loop runs that made it (eval-20261001-163752, eval-20261002-125922) ran the tests, saw the failure and added the missing line. The no-loop run made it and stopped. Only eval-20261001-165125 got both edits before its first test run.
- **On every other case one run was enough.** Across the 43 traces of the first nine runs, 41 used exactly one test run: write the fix, confirm it, stop. In the control, 18 of 19 did. On bugs this size the loop is a safety net, not a search strategy.
- **It cost nothing measurable**: $1.03 with the loop against $1.05 without. Without the test tool the model read a little more (80 tool calls, against 74 besides its 20 test runs), which cancels the turns it skips. Runs vary more than that: the same setup plus one prompt line cost $1.18 (eval-20261001-165125).

### 2. The model
- **Sonnet 5.5: 18 of 19 by the eval, 17 of 19 by hand-check**, at about half the cost.
- **The case the eval couldn't see is tinydb-633.** Sonnet's patch passes every test. It stops the overwrite by skipping IDs that are taken (`while doc_id in table`) but keeps the stale counter. A batch `[Document(id=5), {}, {}]` gets IDs `[5, 1, 2]` (upstream: `[5, 6, 7]`), and a later plain `insert()` can fail with "Document with ID 9 already exists": that happened in 8 of 300 probe trials (10 failed inserts), and never for upstream or any Opus patch. tinydb-633 has no hidden tests, so the eval had no way to see it.
- That's one case, found by hand-checking 3 of the 6 real dev cases, so it's a lead, not a verdict. I kept Opus 5.5 as the default. The fair test is hidden tests for every real case, then Sonnet on the held-out set.

### 3. Hidden tests
- **They're the guard that mattered.** pyflakes-872 passed its visible tests in all 10 runs and failed the hidden one in all 10. Without hidden tests, every setup above would report 19 of 19.
- 10 of the 12 real cases have none (the fix's tests sit in one file, which must stay visible). A fix there is graded on the full visible suite, which is how Sonnet's tinydb patch passed.

### 4. Reading a count of passing tests, not their names
In the first runs (eval-20260930-181610, eval-20261001-131121), pytest's `PASSED` lines were up to 95% of the test output the model read, and they pushed failure tracebacks out of the trimmed tail: 30,122 characters cut from a 26-failure run (cachetools-resize-update), leaving 2 traceback lines. The model now reads one count line (`[757 PASSED lines not shown]`); outcomes are still parsed from the full output. A replay of the five suites in the sandbox gave identical outcomes in all 10 runs, and a passing run shrank from 1,718-10,030 characters to 289-611.

### 5. The guards that never fired
Each of these exists for the worst case. **In the 43 traces of the first nine runs, none of them fired**: 0 refused edits, 0 budget stops, 0 sandbox errors, 0 cut-off replies, 0 refusals. The same held for the 57 traces of the three dev runs since (eval-20261002-124619, -124640, -125922) and the 10 held-out traces: the most expensive fix was $0.18, the longest took 11 turns and 2 test runs. The only one that caught anything since is the final check, which failed the no-loop more-itertools patch (a visible test still failing).

| guard | why it exists | in the 43 traces |
|---|---|---|
| test files and pytest config are read-only; the final check restores them anyway | "make the tests pass" is easiest by changing the tests | 0 refused edits |
| before every call, the output limit is cut to what the remaining budget can pay | a hard dollar ceiling, not a hope: the model doesn't know its budget | 0 budget stops; the most expensive fix was $0.20 of $0.50 |
| a fresh container per test run: no network, 512 MB, 1 CPU, 256 processes, no Linux capabilities, read-only root and workspace, 60 s | the repo's tests are untrusted code | 0 sandbox errors |
| every test that ran before must pass after; a test that disappears counts as unresolved | emptying a parametrize list must not pass for a fix | no patch failed it (the hidden tests caught pyflakes-872) |
| tool calls (40), test runs (8), turns (30) and time (15 min) are capped | a loop that never ends | at most 9 turns, 2 test runs |
| append-only conversation | Opus 5.5 ties its thinking to the exact history, and the prompt cache needs it too | 78% of prompt tokens read from cache |

Since they don't fire on these cases, the guards are tested offline: a scripted model and a fake sandbox drive each one (92 tests, free).

### 6. Cost
- **Cache writes are the cost, not output.** In the control, cache writes were $0.64 of $1.03 (62%), output $0.31 and cache reads $0.08. Every turn adds a tool result, and each addition is written to the cache once at 1.25x the input price, then read back at 0.05x on every later turn. 77-78% of prompt tokens were cache reads.
- **Per fix:** $0.05-0.06 on Opus 5.5, $0.03 on Sonnet 5.5 (dev runs above). The most expensive single fix was boltons-445 at $0.20 (eval-20261001-165125).
- **Spent on the whole build: $6.24 recorded** (the sum of every eval run's results.json), of a $15-20 budget, plus the stalled case's lost calls (at most $0.50; the ledger died with the process).

### 7. Failure handling
- **Network and account failures are counted apart, never as the agent failing.** On 2026-10-02 an empty credit balance failed every call with HTTP 400; eval-20261002-123656 and -123732 recorded 6 API failures and $0.00, graded nothing. The eval now stops a run at the first 400, 401, 403 or 404, since every later case would fail the same way, and lists the rest as not run.
- `--max-total` is a ceiling for a whole run: a case isn't started if its own ceiling could take the run past it.
- A hung docker fails cleanly, and a paid run won't start with the lid closed or the battery under 30%: paid runs were cut short three times by sleep.
- **A stalled API call is bounded now.** On the held-out run, tomlkit-550's log stopped after turn 6 (13:20:19) and stayed silent for 32 minutes, until my session's 40-minute limit on background jobs killed the whole eval at 13:52:54 (runs/eval-20261002-131217/stdout.log and stdout-at-kill.log). The test runs are bounded (about 105 s at worst, even with a hung docker), so the likely stall is the next API call: the SDK allows 10 minutes per attempt with 3 retries, and the fixer's 15-minute limit was only checked between calls. That's not proven, because the trace died with the process. Now each call's timeout is the time left, split across its attempts, so a stalled call ends the fix as an API error, which the eval counts apart and reruns. Three offline tests cover it. The fix landed after the held-out runs, which used the code at b5f812f; it changes only call timeouts, not the prompt, the tools or the limits the model works under.

## Failure taxonomy

Every miss across every graded run: 14 runs, 110 graded case runs (100 dev, 10 held-out), 11 misses, all on the dev set.

| case | runs missed | cause | evidence from the traces |
|---|---|---|---|
| pyflakes-872 | 10 of 10 (every run, every setup) | **second site that doesn't resemble the first** | Every patch fixes `DICT` (checker.py:1830, where the failing test points). The second site is the `TypedDict("a", {...})` call form in `CALL` (checker.py:1608-1611): two calls over `.keys` and `.values`, not a `handleChildren`. In 8 of 10 runs it never appeared in anything the model was shown. In two, the run's first search, made before the model understood the bug, returned it among other matches: checker.py:1609-1610 of 23 lines (eval-20261001-164745) and 1610-1611 of 28 (eval-20261002-125922, `def DICT\|keys\|values`). Neither went back. |
| more-itertools-1285 | 1 (no-loop only) | **no test run to catch a regression** | The first fix (`.get()` instead of a `defaultdict` lookup) broke a different visible test, `BucketTests::test_list`, and the run stopped there. Three loop runs whose first fix broke the same test saw it fail and added the missing line: eval-20261001-163752 and eval-20261002-125922 (the same edit) and eval-20261002-124640 (Sonnet, a different edit). |

| cause | misses |
|---|---|
| second site that doesn't resemble the first | 10 |
| no test run (baseline only) | 1 |
| wrong root cause | 0 |
| special-cased the test | 0 |
| budget | 0 |
| sandbox | 0 |

Not misses, and not counted: 6 API failures from the empty credit balance (above). And one false pass the grader couldn't see: Sonnet's tinydb-633 (section 2).

## Hand-check: passing patches against upstream's fix

Tests passing isn't the same as being the fix. For three real cases I rebuilt upstream's merged fix, the bug, and each run's patch, then ran the same seeded random operations against all of them (`evals/handcheck/handcheck.py`). The bug is the control: a probe that can't tell it from upstream proves nothing.

| case | trials | bug (control) | Opus patches | Sonnet patch | how the Opus patch differs from upstream |
|---|---|---|---|---|---|
| networkx-8895 | 400 | 95 differ | 0 differ (4 runs) | 0 | Adds only the size check to `isomorphisms_iter`. Upstream moves the size **and** degree-sequence checks there from `is_isomorphic`. The degree check is a fast rejection, so answers are identical; speed wasn't measured. |
| more-itertools-1285 | 400 | 256 differ | 0 differ (3 passing runs) | 0 | Keeps the `defaultdict` and registers a key with a bare `self._cache[value]`, the side-effect lookup the issue is about, used on purpose. Upstream replaces the `defaultdict` with a plain dict and `setdefault`, so no lookup can invent a key. Same behavior; upstream removes the trap, the patch works around it. |
| tinydb-633 | 300 | 118 differ, 24 overwrites | 7-8 differ, 0 overwrites, 0 failed plain inserts (4 runs) | 112 differ, 0 overwrites, **10 failed plain inserts** | Fixes the overwrite but hands out different IDs in two edge cases: a batch that failed and wrote nothing still moves the counter (an empty table's next ID is 13, upstream's 1), and after an explicit ID is removed `insert_multiple` and `insert()` disagree (11 vs 2; upstream gives 2 for both, resetting the counter "same as insert()"). |

Opus patches: eval-20261001-163752, -165125, eval-20261002-124619 and -125922 (more-itertools: all but -124619, which missed it). Sonnet: eval-20261002-124640.

A plain insert leaves the ID to tinydb. Inserts that name their own ID fail for every copy, upstream included, whenever the ID is taken. Each Opus tinydb patch has one of those that fails where upstream's succeeds and one the other way round, both from the different numbering above. By the hand-check standard (under "What each design choice bought"), neither counts.

## What didn't work

**Telling the model to search for the same mistake elsewhere.** pyflakes-872 fails because the fix has two sites. So I added one line to the prompt: "Before you stop, search the repo for other code with the same mistake (the same logic, copied or written the same way) and fix it too: the tests may cover only one place."

| | dev solved | pyflakes-872 | runs |
|---|---|---|---|
| without the line | 18 of 19 | 0 of 4 | eval-20260930-181610, -20261001-131121, -163752 (dev); -164745, -164812, -164837 (pyflakes) |
| with the line | 18 of 19 | 0 of 3 | eval-20261001-165125 (dev); -170123, -170207 (pyflakes) |

The model did look ("I looked for the same mistake elsewhere and found none"), but it searched for code shaped like its own fix: `zip\(node`, `def (FOR|COMPREHENSION|GENERATOREXP|LISTCOMP|DICTCOMP|handleChildren)`. The second site doesn't look like the first, so a search for the first's shape can't find it. No gain, no regression, reverted. What might work instead: ask for the *behavior* elsewhere ("where else are dict keys and values visited?"), or give the fix a test that exercises every dict path. Untested.

## Known limits

- **10 of 12 real cases have no hidden tests.** The fix's own tests sit in one file, and that file has to stay visible as the bug report. So a passing fix there is graded by tests the agent could read, and the eval can't see a fix that passes them but behaves wrong. That's exactly how Sonnet's tinydb patch passed. The hand-check is the only deeper check, and it covers 3 cases.
- **Training data.** The planted bugs sit in popular libraries the model has likely seen. The real fixes were merged after its training data, but boltons-445's issue has been public since 2020.
- **One run per setup.** A one-case difference is within what a rerun can change: on more-itertools-1285 Opus's first fix was right in 1 of 4 attempts, and the same setup cost $1.03 in one run and $1.18 (with one extra prompt line) in another.
- **The dev set barely separates setups.** Every setup with the loop scored 18 of 19 by the eval.
- **Python and pytest only**, and only what the sandbox image holds (Python 3.12 and pytest): a repo whose tests need other packages can't run. At most 3,000 files and 30 MB. A fix can't add a file or change a test, even when the test is what's wrong.

## What I'd do next

1. **Write hidden tests for the 10 real cases that have none**, from each upstream fix's behavior (the probes are a start: the tinydb one already catches Sonnet's patch). Then re-grade every saved patch against them. That's free, since grading needs no API calls. The held-out number above stays as reported; new tests apply to runs after it.
2. Hand-check the other real cases the same way.
3. Run Sonnet 5.5 on the held-out set, so the model question gets an answer on cases nobody tuned on.
4. Harder cases: fixes across files, bigger repos, bugs with two sites. The dev set can't tell the setups apart, so more prompt tuning on it would be guessing.

## Likely interview questions

- **How do you know it works?** 10 of 10 on a held-out set I ran once, at the end, after all tuning. With two caveats: 6 of those 10 have no hidden tests, and none was hand-checked. Each case is verified before it counts, graded by hidden tests where it has them, and "fixed" is decided by a fresh sandbox run, not the model. On the dev set, passing patches for three real bugs were compared with upstream's fix on random inputs, and that's what caught Sonnet's tinydb patch.
- **What did the test loop buy?** One case of 19, at no measurable extra cost ($1.03 against $1.05). The first fix there broke a different test in 3 of 4 attempts, and only a test run shows that. On the other 18, one test run confirmed a fix that was already right.
- **Why Opus when Sonnet scores the same for half the price?** It doesn't, once you look past the tests: 18 of 19 by the eval, 17 of 19 by hand-check. Its tinydb patch passes every test and still leaves `insert()` calls that fail, and that case has no hidden tests. It's one case, so the honest answer is "probably Opus, and here's the experiment that would settle it."
- **What's the hardest case and why does it fail?** pyflakes-872: the bug is in two places that don't look alike. The model fixes the one the test points at, every time. Telling it to search elsewhere didn't help, because it searches for the shape of its own fix.
- **How do you stop it gaming the tests?** It can't edit them (refused, and restored before the check anyway), it never sees the hidden ones, and every test that ran before must pass after.
- **How do you cap cost?** Before every call, the output limit is cut to what the remaining budget can pay for once the input is paid. Plus caps on tool calls, test runs, turns and time, a run-wide ceiling in the eval, and $0.50 enforced by the fleet server.
- **What would you do next?** Write hidden tests for the 10 real cases that have none and re-grade every saved patch for free. Then the held-out set on Sonnet, then harder cases. Not more prompt tuning: the dev set can't tell the setups apart.
