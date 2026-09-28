# The Year of AI

One repo, one mission: **12 months, 8 hours a day, become a real AI engineer by shipping in public.**

- **Ceiling:** one of the projects finds users and makes money.
- **Floor:** ~2,000 hours of documented building — a portfolio that gets hired in AI.
- **Rule zero:** ship every 2 weeks. No idea-switching for 12 months.

## Where everything lives

| Path | What it is |
|---|---|
| `curriculum/README.md` | **The plan** — 4 phases, 8–12 projects, the rules. Start here. |
| `curriculum/log.md` | The shipping log — the only metric that matters |
| `apps/health-coach/` | **Project #1**: Coach, an AI personal trainer web app (phone PWA) with memory, tools and a game layer. See its README |
| `apps/research-agent/` | **Project #2**: research agent. Question in, validated and cited brief out (parallel web-search workers, claim-vs-quote validation, cost ledger) |
| `brain/` | The agent fleet's shared memory: goals, registry, learnings (local only, not pushed) |
| `.claude/agents/` | The fleet: `mother` (orchestrator), `outreach-agent`, `health-coach` (local only) |
| `outreach/` | Outbound engine — parked until Phase 3 (local only) |
| `ops/`, `workflows/`, `content/` | Earlier business explorations — superseded, local only |

## The operating loop

1. Build the current project (curriculum says which).
2. Every 2 weeks: ship → log it → post about it.
3. Weekly: *"Mother, run my weekly review"* — she checks the log against the plan and flags drift.
4. New shiny ideas → `brain/goals.md` → Someday. Not this year's problem.
