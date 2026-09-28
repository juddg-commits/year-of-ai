# Design notes: how the research agent works and why

Written to be explained out loud. Every number here was measured on real runs (2026-09-28), not estimated.

## The pipeline

```
question
   │
   ▼
[1] PLAN        Opus 5, structured output  → 2-4 independent sub-questions
   │
   ▼
[2] RESEARCH    Sonnet 5 × N in parallel, server-side web search (≤3 searches each)
   │            → cited prose → CODE extracts evidence: {claim, ≤150-char quote, url}
   ▼
[3] RECOVER     CODE downloads each source page (plain HTTP, no tokens) and extends
   │            cut-off quotes to the end of their sentence
   ▼
[4] VALIDATE    free prechecks, then Opus 5 judges claim-vs-quote (parallel batches)
   │            → supported / partial / unsupported; unsupported is dropped
   ▼
[5] FIT CONTEXT evidence → compact notes with source ids; condense if over budget
   │
   ▼
[6] WRITE       Opus 5, structured brief, [S#] citations
   │
   ▼
   AUDIT        code strips citations to unknown sources, counts uncited sentences
```

**It's a workflow with agentic steps, not one open-ended agent loop.** The order is fixed in code; the model decides inside each step (what to search, what counts as support, how to write). That makes cost, latency and failure modes predictable, and each stage testable alone. An open loop ("research until done") is harder to budget and debug; I'd only reach for it if the task couldn't be decomposed up front.

## The talking points

### 1. Query decomposition
- The planner returns a typed `Plan` (Pydantic → the API's structured outputs), so parsing can't fail on malformed JSON.
- Sub-questions must **stand alone**: each worker sees only its own, so "it"/"they" pointing at another sub-question would break it. That's in the prompt because it's the #1 failure I'd expect.
- Structured outputs can't express "at most 4 items" (no array-length constraints), so the cap is enforced in code. Schema for shape, code for rules.

### 2. Tool design
- Workers use Claude's **server-side** web search: one API call runs search → read → answer on Anthropic's side. `max_uses` caps searches per worker, which is also the cost cap.
- **Finding: the newest search tool drops citations.** `web_search_20260209` adds "dynamic filtering": the model searches from a code sandbox and reads filtered output. The answer came back with **0 citation objects** and took **150 s** per worker. The basic `web_search_20250305` returned **18 cited claims in 29 s** for the same sub-question at the same cost. For an agent built on provenance, I pinned the basic tool.
- Server-side tool loops can stop with `pause_turn`; the worker resumes by re-sending the conversation with the partial turn (capped at 3 resumes).
- Each worker gets a **fresh context**: no shared history, so one sub-question's noise can't pollute another's.

### 3. Citations vs structured outputs (the design constraint)
The API rejects a request that asks for both citations and structured output (400 error). So:
- workers write **cited prose** (citations are automatic with web search),
- **plain code** turns citations into evidence records (deterministic, free, unit-tested),
- the orchestrator steps (validate, write) use **structured output** over that evidence.

The API attaches each citation to the exact quoted span, often a fragment ("increases in total Mini-Z (…)"). Code widens it to the full sentence using the surrounding text, so each claim stands alone but stays close to its quote.

### 4. Dealing with noisy web data
- Workers are told to prefer primary sources, state disagreements, and write `GAP:` instead of guessing.
- **Uncited text never reaches the brief.** Prose without a citation is logged in the trace as "uncited" (opinion or memory) and excluded.
- URLs are normalized (tracking params, `www`, trailing slash) so the same page counts once; the same claim from *different* pages is kept, because that's corroboration.
- Search errors arrive as HTTP 200 with an error object instead of results; they're recorded, not crashed on.

### 5. Hallucination reduction: four layers
1. **Cite or it doesn't count** (worker prompt + extraction code).
2. **Validator: does THIS quote support THIS claim?** A separate model judges each claim against its quote. It's told *not* to use its own knowledge, because the check is support, not truth. It **fails closed**: an item the validator skipped counts as unsupported. It caught real misattributions, e.g. a Cleveland Clinic statistic whose citation pointed at a Mass General Brigham quote.
3. **The writer only sees validated notes** and must cite `[S#]` on every factual sentence; "partial" notes must be hedged.
4. **Programmatic audit**: code strips citations to sources that don't exist and counts uncited sentences. Latest run: **45/46 sentences cited, 0 invented sources.**

### 6. Context window management
- A single search pulls ~17-20k tokens of page content. One run read **~194k tokens** of search results.
- The orchestrator **never sees raw pages**, only compact notes (claim + one-sentence quote + source id): **~11.6k tokens** for the same run, a 94% reduction.
- If notes exceed the budget (20k tokens), they're condensed **per sub-question** by the cheap model, and each condensed note must list which sources it came from. Code rejects any source id the condenser invents, so **citations survive compression**. That's the difference from blindly summarizing old context.

### 7. Cost and latency engineering
Same question, before and after tuning:

| | first run | tuned |
|---|---|---|
| total | $1.68, 6 min 40 s | **$1.25, 2 min 29 s** |
| research (Sonnet, 12 searches) | $0.70 | $0.66 |
| validate (Opus) | $0.54, 152 s | **$0.35, 31 s** |
| compress | $0.15, 87 s | skipped (fits budget) |
| write (Opus) | $0.26 | $0.20 |

What changed: validator effort `medium` → `low` (it's classification with a clear rubric; the cost was thinking tokens), validator batches in parallel, a realistic context budget. **Measure per stage first**; the first guess about where money goes is usually wrong.
- **Orchestrator-worker split:** Opus for the few judgment-heavy calls, Sonnet for the token-heavy research (~2.5x cheaper per token).
- **Prompt caching isn't worth it here:** the shared prefix across workers is ~1k tokens, while the 194k tokens of search results are unique per worker. Caching pays when a big prefix repeats; that's not this workload.
- Cheaper runs: `--searches 2` or `--sub-questions 3`.

### 8. Failure handling
- One worker failing doesn't sink the brief; its error goes in the trace.
- Opus calls opt into server-side refusal **fallbacks** (`fallbacks: "default"`); refusals and `max_tokens` cut-offs raise clear errors instead of parsing garbage.
- Every run writes a JSON **trace** (plan, queries, every evidence item + verdict, calls, cost). Failed runs still save what they did and what they cost.

### 9. Measure before you build: the cut-off quote fix
Too many claims came back "partial" (56%). The plan was to send the validator every quote for a claim at once. Before building that, I counted the causes across three runs:

| why a claim was "partial" | share of partials |
|---|---|
| its quote was **cut off**: the API caps quotes at ~150 chars and ends them with "..." | **76-88%** |
| its claim had 2+ quotes judged separately (the planned fix) | 34-54%, mostly *also* cut off |

The planned fix would have barely moved the number. The worker had read the whole page; only our excerpt was short. So stage 3 downloads the source page with a plain HTTP request (no tokens, 3-9 s per run), finds the quote in it and completes the sentence (`quotes.py`).
- **Matching on words, not characters.** The API's quote is markdown and the page isn't, so the quote's words must appear in order with anything non-word between them. The last word is skipped because it's often half a word ("documentatio").
- **Real data found the edge cases the first tests missed:** Wikipedia quotes carry link targets and `[7]` markers, and the API sometimes glues two separate excerpts into one quote. Handling both raised recovery on one run from 61 to 73 of 81 cut-off quotes. What's left is mostly pages that can't be downloaded (403s, PDFs).
- **Controlled replay** (`revalidate.py`): the same claims and validator prompt, API quotes vs recovered quotes, research not re-run.

| | supported | partial | unsupported |
|---|---|---|---|
| AI scribes, API quotes → recovered | 31% → **54%** | 64% → **41%** | 5% → 5% |
| open weights, API quotes → recovered | 32% → **46%** | 60% → **50%** | 8% → **4%** |

The second replay separates claims whose quote was recovered from the rest: recovered quotes improved 30 verdicts and worsened 1. That one bundles a second fact the full sentence doesn't contain, so "partial" is right: the validator got *more* accurate, not less. (The first replay, run before that split existed: 31 improved, 4 worse, at least one of them noise.)
- **The judge isn't independent per item.** In the second replay, 10 of the 48 claims whose quote didn't change still changed verdict, all toward stricter. A cut-off quote looks weaker next to complete ones in the same batch. So one replay's small differences are noise, and an eval harness has to repeat runs (Exercise 3).

## Known issues (honest list)
- **"Partial" is still 41-50%.** The main remaining cause: claims that bundle several facts from different sentences, each with its own quote. → Exercise 1. Pages that block downloads (403) or are PDFs keep their cut-off quote. → Exercise 4.
- **The validator's verdicts depend on the batch** (see §9): a claim's verdict can change when its neighbors change.
- **The brief ignores its length target** (asked for 600-900 words, wrote ~2,000). Length in a prompt is a soft constraint. → Exercise 2.
- `len(text) // 4` is a rough token estimate; fine for a budget decision, not for billing (billing uses the API's usage numbers).

## Exercises: build these yourself
1. **Claim-level validation.** Group evidence by claim and send the validator *all* quotes for a claim at once, returning which quote ids support it. Now that quotes are full sentences, this targets the main remaining cause of "partial". Measure it with `revalidate.py`. (`validator.py`, `evidence.py`)
2. **Enforce brief length.** Put a word budget in the `Section.body` field description and add a post-check that re-asks once if it's over. Measure whether it works.
3. **Eval harness (Phase 2, project 6).** Pick 10 questions, save their traces, and score them: citation precision (sample claims, check the source), coverage, cost, latency. Re-run after every change, and run validation 3 times per trace so you know how big a change has to be before it's more than noise.
4. **Recover quotes from PDFs.** arXiv and journal PDFs keep their cut-off quotes today. Extract the text (e.g. `pypdf`) in `pages.fetch_page` and reuse `extend_quote`.

## Likely interview questions
- **Why not one agent with a search tool in a loop?** Predictability. Decomposition up front means parallel workers, a fixed cost ceiling (sub-questions × searches), and stages I can test and measure separately.
- **How do you know it isn't hallucinating?** Four layers (above), plus numbers: 45/46 sentences cited, 6 misattributed claims caught and dropped, 0 invented citations, all in the trace.
- **What was the hardest bug?** Zero evidence on the first run: the newest search tool routes results through a code sandbox and returns no citations. Found it by dumping raw response blocks for one worker.
- **Tell me about measuring before building.** The plan to fix "partial" verdicts targeted multi-quote claims. Counting the causes first showed 76-88% came from the API's 150-char quote cap instead. Recovering the full sentence from the page, in plain code, cut "partial" from 64% to 41% on a controlled replay.
- **How did you cut cost?** Per-stage ledger first. Validation was 32% of cost for a classification task: lower effort + parallel batches cut it 35% and 5x on latency.
- **How do you manage context?** Workers are isolated; the orchestrator only sees compact evidence (~94% smaller than raw results); compression keeps provenance.
- **What would you do next?** An eval harness (exercise 3) before any more prompt tuning: without it, every change is a vibe check.
