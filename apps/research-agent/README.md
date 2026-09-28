# Research agent: question in, cited brief out

Ask a hard question. The agent splits it into sub-questions, researches them in parallel with web search, checks every claim against the quote that's supposed to back it, and writes a brief where every sentence cites its source.

Project #2 of my Year of AI. Python, Claude API (Opus 5 orchestrates, Sonnet 5 researches).

```
$ python research.py --sub-questions 2 --searches 1 "What does the evidence say about whether four-day work weeks maintain productivity?"
[1/6] Planning…              2 sub-questions
[2/6] Researching…           44 citations → 42 evidence items from 14 sources
[3/6] Recovering quotes…     16/25 cut-off quotes extended from 12 pages (3 blocked, http 403)
[4/6] Validating…            22 supported, 17 partial, 3 dropped, 2 conflicts
[5/6] Context…               ~3.8k tokens of notes → fits, no compression
[6/6] Writing…               33/33 sentences cited, 0 invalid citations

stage        calls  searches    in tok  out tok     cost
plan             1         0       952      772   $0.024
research         2         2    29,109    4,649   $0.125
validate         2         0     9,711    2,748   $0.117
synthesize       1         0     6,441    2,704   $0.100
total                                             $0.366
```

A full-size brief (4 sub-questions × 3 searches, $1.25): [examples/ai-scribes.md](examples/ai-scribes.md).

## How it works

1. **Plan**: break the question into independent sub-questions (structured output).
2. **Research**: one worker per sub-question, in parallel, using Claude's server-side web search. Code turns every citation into an evidence record: claim, quote, URL.
3. **Recover quotes**: the API cuts quotes off at ~150 characters, often right before the number a claim depends on. Code downloads the source page, finds the quote, and completes its sentence. Plain HTTP, no tokens.
4. **Validate**: a separate model checks each claim against its quote; unsupported claims are dropped.
5. **Fit context**: the writer sees compact evidence notes, never raw pages (~94% smaller); notes are condensed if they outgrow the budget, keeping their sources.
6. **Write + audit**: a structured brief with `[S#]` citations; code removes citations to unknown sources and counts uncited sentences.

Step 3 cut "partial" verdicts from 64% to 41% on the same evidence (a controlled replay with `revalidate.py`).

Why each piece is built this way, with measurements: **[DESIGN.md](DESIGN.md)**.

## Run it

Needs Python 3.10+ and an [Anthropic API key](https://console.anthropic.com/settings/keys).

```bash
cd apps/research-agent
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                      # paste your ANTHROPIC_API_KEY
.venv/bin/python research.py "your question"
.venv/bin/python research.py --searches 2 --sub-questions 3 "your question"   # cheaper
.venv/bin/python -m unittest discover tests                                   # offline tests, free
.venv/bin/python revalidate.py runs/<run>.json     # replay validation: API quotes vs recovered quotes (~$0.70)
```

Each run saves the brief (`runs/*.md`) and a full JSON trace (`runs/*.json`): plan, search queries, every evidence item with its verdict, every API call and its cost.

**Cost:** about $1.25 and 2-3 minutes per question with the defaults (4 sub-questions × 3 searches). Research is about half the cost, because each search reads ~17-20k tokens of pages.

## Layout

```
research.py              CLI
revalidate.py            replay validation on a saved run, with and without recovered quotes
research_agent/
  config.py              models, budgets, prices
  llm.py                 API calls + the cost ledger (every call is measured)
  planner.py             [1] query decomposition
  researcher.py          [2] parallel web-search workers + citation extraction
  pages.py               [3] download source pages, recover cut-off quotes
  quotes.py              [3] find a quote on its page and complete the sentence
  validator.py           [4] claim-vs-quote validation
  evidence.py            [5] sources, dedupe, context budget, compression
  synthesizer.py         [6] the brief + citation audit
  agent.py               the pipeline
  report.py              brief markdown, cost table, trace
tests/test_offline.py    deterministic steps, no API calls
```

## License

MIT
