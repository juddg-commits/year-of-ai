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

## 2026-09-28 — Research agent: question in, cited brief out (Phase 1, ship #2)
- Repo: https://github.com/juddg-commits/year-of-ai (`apps/research-agent/`)
- What it does: splits a question into sub-questions, researches them in parallel with web search, recovers each cut-off quote from its source page, checks every claim against its quote, and writes a brief where every sentence cites a source (~$1.25, ~2.5 min per question).
- What broke / what I learned: the newest search tool returned zero citations, so I pinned the older one (found by dumping raw responses). A per-stage cost ledger showed validation, a simple grading job, eating a third of the cost and most of the time ($1.68 → $1.25 after lowering its effort and parallelizing). Measuring before building: "partial" verdicts came from the API's 150-char quote cap, not multi-quote claims (recovering the sentence: 64% → 41% partial). An LLM judge isn't independent per item: verdicts shift with batch context.
- Post: _pending: edit WRITEUP.md and post it, then paste the link here_

## 2026-09-28 — Coach: AI personal trainer (Phase 1, ship #1)
- Repo: https://github.com/juddg-commits/year-of-ai (`apps/health-coach/`)
- What it does: a phone-first web app where you talk to a coach that logs workouts, meals, weigh-ins and plans through tools, programs your week from real logged loads, and pays XP only for what's in the logs.
- What broke / what I learned: the agent loop is small (decide → call tool → read result → continue); the rest is plumbing. A YouTube key from AI Studio (`AQ.`) silently failed for a month, so now errors get logged, not swallowed. Two QA passes found 25 bugs, most in state that outlives one request (reloads, midnight, double-logging). A stakes preview has to use the exact same rules as the payout or it lies. Shipped a month late because nothing was committed until the end: commit on day one.
- Post: _pending: edit WRITEUP.md and post it, then paste the link here_
