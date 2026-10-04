# Shipping Log — the only metric that matters

One entry per ship. Every 2 weeks, no exceptions. Newest on top.
An empty fortnight here is the alarm bell — if it happens, the weekly review's first question is "what blocked shipping?"

Format:

```
## YYYY-MM-DD — Project name (Phase N, ship #N)
- Repo: <GitHub link>
- What it does: <one line>
- What broke / what I learned: <the honest version — this is the valuable part>
- Post: <link to the public write-up/post>
```

---

## 2026-10-02 — Code fixer: failing tests in, checked patch out (Phase 2, ship #3)
- Repo: https://github.com/juddg-commits/year-of-ai (`apps/code-fixer/`)
- What it does: copies a small Python repo whose tests fail, lets Claude (Opus 5.5) investigate and edit it with five tools, runs every test in a locked-down container, and decides "fixed" with a fresh sandbox run with the tests restored, never the model's word. Held-out: 10 of 10 by the eval, about $0.08 and 43 s per fix (6 of the 10 are real bugs with no hidden tests, so it's an upper bound). Mother can call it through the fleet server (`fix_code`, capped at $0.50).
- What broke / what I learned: hidden tests were the guard that mattered. pyflakes-872 passed its visible tests and failed the hidden one in 10 of 10 runs, because the bug sits in two places that don't look alike, and telling the model to search elsewhere didn't help. The same score isn't the same fix: Sonnet 5.5 matched Opus at 18 of 19 for half the price, but a hand-check against upstream's fix found its tinydb patch makes inserts fail (17 by hand-check). Taking the test tool away cost one case and saved nothing. The other guards never fired in 43 traces. A time limit checked between calls doesn't bound a call: one stalled API call hung the held-out run for over 30 minutes.
- Post: _pending: edit WRITEUP.md and post it, then paste the link here_

## 2026-09-28 — Research agent: question in, cited brief out (Phase 1, ship #2)
- Repo: https://github.com/juddg-commits/year-of-ai (`apps/research-agent/`)
- What it does: splits a question into sub-questions, researches them in parallel with web search, recovers each cut-off quote from its source page, checks every claim against its quote, and writes a brief where every sentence cites a source ($1.25 and 2.5 min on the tuned run, runs/20260928-092603).
- What broke / what I learned: the newest search tool returned no citations, so I pinned the older one (found by dumping raw responses). A per-stage cost ledger showed validation, a simple grading job, was a third of the cost and the slowest stage; lower validator effort, parallel batches, a realistic context budget and a brief-length target took a question from $1.68 to $1.25. Measuring before building: 76-88% of "partial" verdicts came from the API's 150-char quote cap, not multi-quote claims (recovering the sentence: 66% → 54% partial on a saved replay, runs/revalidate-20261004-140048). An LLM judge isn't independent per item: verdicts shift with batch context.
- Post: _pending: edit WRITEUP.md and post it, then paste the link here_

## 2026-09-28 — Coach: AI personal trainer (Phase 1, ship #1)
- Repo: https://github.com/juddg-commits/year-of-ai (`apps/health-coach/`)
- What it does: a phone-first web app where you talk to a coach that logs workouts, meals, weigh-ins and plans through tools, programs your week from real logged loads, and pays XP only for what's in the logs.
- What broke / what I learned: the agent loop is small (decide → call tool → read result → continue); the rest is plumbing. A YouTube key from AI Studio (`AQ.`) silently failed for a month, so now errors get logged, not swallowed. Two QA passes found 25 bugs, most in state that outlives one request (reloads, midnight, double-logging). A stakes preview has to use the exact same rules as the payout or it lies. Shipped a month late because nothing was committed until the end: commit on day one.
- Post: _pending: edit WRITEUP.md and post it, then paste the link here_
