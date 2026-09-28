# Ship #2: a research agent that cites every sentence

This is project #2 of my Year of AI, where I'm spending a year building AI projects in public.

**What it does:** you ask it a hard question, like "are AI medical scribes actually reducing doctor burnout?" It breaks the question into smaller ones, researches them on the web at the same time, checks every claim against the exact quote it came from, and writes a short brief where every sentence has a source. One question costs about $1.25 and takes 2 to 3 minutes.

I built it with Claude Code as my pair programmer. It wrote most of the code. I picked what to build, tested it on my own questions, and decided which problems were worth fixing.

## What broke

**It found zero evidence the first time.** The newest version of the web search tool routes everything through a code sandbox, and it came back with no citations at all. We only figured that out by looking at the raw response. The older search tool gave 18 cited claims in 29 seconds on the same test, so I switched back.

**Most claims came back "partly supported," and my planned fix was wrong.** I was going to have it check each claim against all of its quotes at once. Before building that, we counted why claims were failing. 76 to 88% of the time, the quote had been cut off at 150 characters, usually right before the number the claim was about. So now the agent opens the actual web page and grabs the full sentence. Same evidence, and "partly supported" dropped from 64% to 41%.

**The first run cost $1.68 and took almost 7 minutes.** Tracking the cost of every step showed something I didn't expect: the checking step, which is basically a grading job, was eating a third of the cost and most of the time. Turning down how hard it thinks and running the checks in parallel got a question to $1.25 and about 2 and a half minutes.

## What I learned

- **Look at the data before you fix anything.** My fix for the "partly supported" problem was aimed at the wrong cause, and I only found out because we counted first.
- **AI grading isn't as consistent as it looks.** When I re-ran the checker, 10 claims whose evidence hadn't changed at all still got different grades, probably because the claims graded alongside them had changed. One run isn't proof of anything.
- **A fixed step-by-step pipeline is easier to trust than letting the AI do whatever it wants.** I always know what a question costs and exactly which step broke.

## What's next

This agent is the first member of a small team. I'm connecting all my agents under one "Mother" agent that decides which one to use, and she can already call this one like a tool. Next up is an agent that fixes code and proves it worked by running the tests.

Code: https://github.com/juddg-commits/year-of-ai/tree/main/apps/research-agent
