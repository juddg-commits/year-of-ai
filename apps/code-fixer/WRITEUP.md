# Ship #3: a code fixer that has to prove its fix

This is project #3 of my Year of AI, where I'm spending a year building AI projects in public.

**What it does:** you point it at a small Python project whose tests are failing. It copies the project, runs the tests in a locked-down container, and lets Claude read the code and make edits. Then it checks the fix itself, in a fresh container, with the original tests put back. It never touches your project. You get a patch to review and a log of every step. On the held-out cases, a fix cost about 8 cents and took 43 seconds on average.

I built it with Claude Code as my pair programmer. It wrote most of the code. I chose the design, set the budget, and decided what a fix had to prove before it counted.

**How I know it works:** I built 29 test cases. 15 are bugs I planted in popular open-source libraries, 12 are real bugs that were fixed on GitHub after the model's training data, with the fix undone, and 2 I wrote by hand. I tuned on 19 of them and set 10 aside. On those 10, run once at the end, it fixed all 10 by the tests they had. But 6 of the 10 were real bugs with no hidden tests, graded only by tests the agent could read. So afterward I wrote hidden tests for every real bug and re-graded the saved fixes, which costs nothing. 9 of 10 still pass. I haven't hand-checked those patches against the real fixes yet.

## What broke

**On one bug, the tests passed and the fix was still wrong, every time.** The pyflakes bug sits in two places that don't look alike. The agent fixed the one the failing test pointed at, and in 9 of 10 runs it reported that the tests pass. They did. A hidden test it never saw caught the second place: 10 runs, 10 misses. So I told it to search for the same mistake elsewhere. It did search, but for code shaped like its own fix, so it never found the second place. Same score with the line as without it, so I took it out.

**A held-out fix passed every test it had and fixed the wrong place.** On the lark bug, a copy of a parser kept sharing state with the original. The agent fixed the copy method the failing test calls. The real cause was in another file, and upstream fixed it there. The hidden test I wrote later copies the parser's state directly, and that path still shared it. Same pattern as the pyflakes bug: it fixed where the test pointed.

**The test output it read was mostly noise.** In the first runs, lines saying a test passed were up to 95% of the test output the model read. In one run with 26 failures, they pushed most of the failure details out of view. Now it reads one line with the count.

**The final eval run hung for over half an hour.** On the 10th held-out case, the log went silent for 32 minutes, most likely on a stalled call to the model. The agent had a 15-minute limit, but it only checked it between calls, so nothing stopped the wait until my session killed the whole run. I reran that one case alone, with the same code, and it passed. Now every call's timeout is the time the fix has left.

**My account ran out of credit, and the eval kept going anyway.** Every case failed with the same error, one after another. Nothing was charged, but it should have stopped at the first one, so now it does.

## What I learned

- **The same score isn't the same fix.** Sonnet 5.5 matched Opus 5.5 at 18 of 19 practice cases for about half the price. Then I checked the patches against the real fixes on random inputs. Sonnet's fix for the tinydb bug passed every test and still made plain inserts fail in 8 of 300 trials. That case had no hidden tests, so the eval had no way to see it. By hand-check, Sonnet got 17, and the hidden test I wrote later catches it too.
- **Taking a piece away is how you find out what it does.** Without the ability to run tests, the agent solved 17 instead of 18, at about the same cost. On one bug, Opus's first fix broke a different test 3 times out of 4. Only running the tests shows you that.
- **The safety checks almost never fired.** Read-only tests, the dollar cap, the sandbox limits: in 100 attempts on the practice cases, none of them was needed. The final check caught one bad patch, in the run without the test tool. That doesn't make the guards pointless. It means these bugs are easy for the model. What caught the misses the agent couldn't see was the hidden tests.

## What's next

Every real bug has hidden tests now. Next is running Sonnet on the held-out set, to settle the model question on cases nobody tuned on. Then harder bugs. Mother, the agent that runs my fleet, can now call it as a tool, capped at 50 cents a fix.

The whole build cost $6.24 in recorded API calls, plus at most 50 cents lost with the stalled run.

Code: https://github.com/juddg-commits/year-of-ai/tree/main/apps/code-fixer

Every number here comes from a saved run, and [DESIGN.md](DESIGN.md) names the run behind each one. Most stay on my machine, but the held-out runs and the re-grade are in [sample-runs/](sample-runs/).
