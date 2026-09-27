# The Year of AI — 12 months, 8 hours a day, ship in public

**Mission:** become a genuinely skilled AI engineer through 8–12 shipped, public projects.
**Ceiling:** one of them finds users and makes money.
**Floor:** ~2,000 hours of documented building — a portfolio that gets you hired in AI.
Both outcomes are wins. The only losing move is not shipping.

## The Rules (non-negotiable)

1. **Build first.** You never "study" a topic — you build something that *needs* it, then learn what the build demands. Tutorial hell is the #1 way self-teachers waste a year.
2. **Ship every 2 weeks.** "Shipped" = code on GitHub + a README + it actually runs + a short write-up of what broke and what you learned. Log every ship in `log.md`.
3. **Public by default.** Every ship gets a post (X/LinkedIn): "I built X, here's what I learned." This is your accountability, your network into AI jobs, and your future distribution — technical build-in-public is far less crowded than business-guru content.
4. **This is the last fork.** No idea-switching for 12 months. New ideas go in `brain/goals.md` under Someday — they don't get to steal the quarter.
5. **Weekly review.** Once a week, run: *"Mother, run my weekly review"* — she reads `log.md` + `brain/goals.md`, checks the cadence, and flags drift.

---

## Phase 1 — Foundations (Months 1–2)

**Goal:** total comfort with the core API stack + GitHub habits. 3 shipped projects.

**Skills:** API calls, streaming, system prompts, multi-turn state, file-based memory, client-side tool use, structured outputs, prompt caching basics, token/cost accounting, git + GitHub hygiene.

| # | Project | Weeks | What it teaches |
|---|---------|-------|-----------------|
| 1 | **Health Coach CLI** (`apps/health-coach/`) — finish it, run it daily, then extend it: give the model *tools* (`log_workout`, `save_note`) it can call, and a `/summary` command using structured outputs for a typed weekly report | 1–2 | The whole request loop: system prompt, streaming, conversation state, memory on disk → then tool use + structured outputs |
| 2 | **Research CLI** — ask a question, get a cited answer: uses the API's server-side web search tool + citations; add prompt caching and print cost per query | 3–4 | Server-side tools, citations, caching, cost accounting |
| 3 | **First web app** — wrap one of your assistants in a real interface (FastAPI + simple frontend), deploy it publicly (Railway/Render/Vercel) | 5–8 | Shipping software people can touch: routes, state, deploys, secrets. Your first live URL |

**Exit test:** you can build a working AI app from a blank folder, from memory, in an afternoon.

## Phase 2 — The Agent Stack (Months 3–4)

**Goal:** the skills that actually distinguish AI engineers in the market. 3 shipped projects.

**Skills:** embeddings, chunking, vector search, retrieval quality, agent loops, MCP protocol, **evals** (golden datasets, automated grading, regression tracking) — evals are the single most underrated separator between pros and tinkerers — plus basic observability/tracing.

| # | Project | Weeks | What it teaches |
|---|---------|-------|-----------------|
| 4 | **RAG app over a real corpus** (e.g. your own `brain/`, or a domain's docs) — measure retrieval quality, don't just vibe it | 9–12 | Embeddings, chunking strategy, vector DB, recall@k, why RAG fails |
| 5 | **Build & publish an MCP server** (PyPI/npm) — a tool other people can plug into Claude Code; you use MCPs daily, now you make one | 13–14 | The MCP protocol, tool design, packaging, your first installable artifact |
| 6 | **Agent + eval harness** — an agent that does a real multi-step task, with a golden test set and automated grading proving it works (and catching regressions) | 15–16 | Agent loops, tool orchestration, evals — the resume line most candidates can't back up |

**Exit test:** you can explain — because you've *measured it* — when RAG beats long context, and show an eval dashboard for an agent you built.

## Phase 3 — Depth + First Paid Work (Months 5–7)

**Goal:** a lane, a reputation, and first AI income.

- **Pick your lane** (agents, RAG/search, evals, voice — whichever project grabbed you hardest) and go deep.
- **Open-source one tool** in that lane that strangers actually use. Users/stars = public proof.
- **Round out the stack:** run open models locally (Ollama), do one small fine-tune (LoRA) so you understand the other half of the field.
- **First contracts:** 2–3 small freelance AI-automation gigs. Point your own `outreach-agent` at getting *you* clients — the outbound engine gets its real use here. Real client problems are the fastest teacher and your first AI income.
- **Write weekly** — technical posts in your lane. By month 7 you should be "the person who writes about X."

## Phase 4 — Capstone (Months 8–12)

**Goal:** one serious product in your lane, built to production standards: real users, cost/latency budgets, monitoring, guardrails.

- Best case: it earns → you have a business.
- Base case: it's the portfolio centerpiece → you apply to AI engineering roles with 8–12 shipped repos, a published MCP server, an OSS tool with users, paid contract work, and a year of public write-ups. That's not "aspiring" — that's a working AI engineer's track record.
- Job motion runs in parallel from month 9: warm network from your content first, applications second, always leading with the work.

---

## Cadence at a glance

- **Daily:** ~6h build / ~1h read-around-the-build / ~1h write & post.
- **Every 2 weeks:** ship + log in `log.md` + public post.
- **Weekly:** Mother-led review against this file.
- **Every phase end:** update `brain/knowledge/` with what the phase taught; adjust the next phase — the plan serves the mission, not the reverse.
