# Ship #2: a research agent that cites every sentence

This is project #2 of my Year of AI, where I'm spending a year building AI projects in public.

**What it does:** you ask it a hard question, like "are AI medical scribes actually reducing doctor burnout?" It breaks the question into smaller ones, researches them on the web at the same time, checks every claim against the exact quote it came from, and writes a short brief where every sentence has a source. On my tuned run, that question cost $1.25 and took two and a half minutes.

I built it with Claude Code as my pair programmer. It wrote most of the code. I picked what to build, tested it on my own questions, and decided which problems were worth fixing.

## What broke

**It found nothing to cite the first time.** The newest version of the web search tool routes everything through a code sandbox, and it came back with no citations at all. We only figured that out by looking at the raw response. The older search tool returned cited claims on the same test, so I switched back.

**Most claims came back "partly supported," and my planned fix was wrong.** In each of my first three full runs, over half the claims got that grade. I was going to have it check each claim against all of its quotes at once. Before building that, we counted why claims were failing. In 76 to 88% of them, the quote had been cut off: the search tool caps quotes at 150 characters. Claims with more than one quote, the ones my fix was for, were a third to a half of them, and most of those were cut off too. So now the agent opens the actual web page and pulls the full sentence. On a controlled replay of the same 116 claims, "partly supported" dropped from 66% to 54%.

**The first full run cost $1.68 and took almost 7 minutes.** Tracking the cost of every step showed something I didn't expect: the checking step, which is basically a grading job, was a third of the cost and the slowest step. A handful of changes got a question to $1.25 and two and a half minutes. The checker thinks less hard and runs its batches in parallel, the notes got a realistic size limit so a condensing step stopped running, and the brief got a length target. The checker was the biggest single saving, but less than half of it.

## What I learned

- **Look at the data before you fix anything.** My fix for the "partly supported" problem was aimed at the wrong cause, and I only found out because we counted first.
- **AI grading isn't as consistent as it looks.** In the same replay, 5 claims whose quote hadn't changed at all still got a stricter grade, probably because the claims graded alongside them had changed. One run isn't proof of anything.
- **A fixed step-by-step pipeline is easier to trust than letting the AI do whatever it wants.** I always know what a question costs and exactly which step broke.

## What's next

This agent was the first member of a small team. Mother, the agent that runs my fleet, can call it like a tool, and a second agent has joined since: a code fixer that has to prove its fix by running the tests (ship #3).

Code: https://github.com/juddg-commits/year-of-ai/tree/main/apps/research-agent

Every number here comes from a saved run: the first run `runs/20260928-092235`, the tuned run `runs/20260928-092603`, a third question with the same settings `runs/20260928-093154`, and the replay `runs/revalidate-20261004-140048/` ($0.69). The replay is in [sample-runs/](sample-runs/), and the rest stay on my machine. [DESIGN.md](DESIGN.md) has the details.
