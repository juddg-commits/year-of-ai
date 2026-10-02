# Ship #3: a code fixer that has to prove its fix

This is project #3 of my Year of AI, where I'm spending a year building AI projects in public.

**What it does:** you point it at a small Python project whose tests are failing. It copies the project, runs the tests in a locked-down container, and lets Claude read the code and make edits. Then it checks the fix itself, in a fresh container, with the original tests put back. It never touches your project. You get a patch to review, and a log of every step. A fix costs about 5 cents and takes under half a minute.

I built it with Claude Code as my pair programmer. It wrote most of the code. I chose the design, set the budget, and decided what a fix had to prove before it counted.

**How I know it works:** I built 29 test cases. Some are bugs I planted in popular open-source libraries. The rest are real bugs that were fixed on GitHub after the model's training data, with the fix undone. I tuned on 19 of them and set 10 aside. On those 10, run once at the end, it fixed all 10. I treat that as an upper bound: 6 of the 10 are real bugs with no hidden tests, so they're graded only by tests the agent could read.

## What broke

**On one bug it said "all tests pass" every time, and every time it was wrong.** The pyflakes bug sits in two places that don't look alike. The agent fixed the one the failing test pointed at, and a test it never saw caught the other. 10 runs, 10 misses. So I told it to search for the same mistake elsewhere. It did search, but for code shaped like its own fix, so it never found the second place. Same score with the line as without it, so I took it out.

**The test output it read was mostly noise.** In the first runs, 59 to 95% of what the model saw was lines saying a test passed, and they pushed the actual failures out of view. Now it reads one line with the count.

**The final eval run hung for half an hour.** On the 10th held-out case, a call to the model most likely stalled (the log just stops). The agent had a 15-minute limit, but it only checked it between calls, so nothing stopped the wait until my session killed the whole run. I reran that one case, and it passed. Now every call's timeout is the time the fix has left.

**My account ran out of credit, and the eval kept going anyway.** Every case failed with the same error, one after another. Nothing was charged, but it should have stopped at the first one, so now it does.

## What I learned

- **The same score isn't the same fix.** Sonnet 5.5 matched Opus 5.5 at 18 of 19 for half the price. Then I checked the patches against the real fixes on random inputs. Sonnet's fix for the tinydb bug passed every test and still made inserts fail in 8 of 300 trials. That case has no hidden tests, so the eval had no way to see it. By hand-check, Sonnet got 17.
- **Taking a piece away is how you find out what it does.** Without the ability to run tests, the agent solved 17 instead of 18, at about the same cost. On one bug, its first fix broke a different test 3 times out of 4. Only running the tests shows you that.
- **The safety checks never fired.** Read-only tests, the dollar cap, the sandbox limits: in 100 attempts on the practice cases, none of them was needed. That doesn't make them pointless. It means these bugs are easy for the model. The check that caught the misses the agent couldn't see was the hidden tests.

## What's next

10 of the 12 real bugs have no hidden tests, so the first job is writing them and re-grading every saved patch, which costs nothing. Then harder bugs. The fleet server now offers it as a tool to Mother, the agent that runs my fleet.

Code: https://github.com/juddg-commits/year-of-ai/tree/main/apps/code-fixer
