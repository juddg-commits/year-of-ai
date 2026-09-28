# Research agent: question in, cited brief out

Ask a hard question. The agent splits it into sub-questions, researches them in parallel with web search, checks every claim against the quote that's supposed to back it, and writes a brief where every sentence cites its source.

Project #2 of my Year of AI. Python, Claude API (Opus 5 orchestrates, Sonnet 5 researches).

```
$ python research.py "How are US hospitals using AI medical scribes, and is there evidence they reduce clinician burnout?"
[1/5] Planning…              4 sub-questions
[2/5] Researching…           117 citations → 116 evidence items from 61 sources
[3/5] Validating…            45 supported, 65 partial, 6 dropped, 2 conflicts
[4/5] Context…               ~11.6k tokens of notes → fits, no compression
[5/5] Writing…               45/46 sentences cited, 0 invalid citations

stage        calls  searches    in tok  out tok     cost
plan             1         0       961    1,171   $0.034
research         4        12   194,015   15,632   $0.664
validate         3         0    29,482    8,167   $0.352
synthesize       1         0    17,398    4,359   $0.196
total                                             $1.246
```

Full output: [examples/ai-scribes.md](examples/ai-scribes.md).

## How it works

1. **Plan**: break the question into independent sub-questions (structured output).
2. **Research**: one worker per sub-question, in parallel, using Claude's server-side web search. Code turns every citation into an evidence record: claim, quote, URL.
3. **Validate**: a separate model checks each claim against its quote; unsupported claims are dropped.
4. **Fit context**: the writer sees compact evidence notes, never raw pages (~94% smaller); notes are condensed if they outgrow the budget, keeping their sources.
5. **Write + audit**: a structured brief with `[S#]` citations; code removes citations to unknown sources and counts uncited sentences.

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
```

Each run saves the brief (`runs/*.md`) and a full JSON trace (`runs/*.json`): plan, search queries, every evidence item with its verdict, every API call and its cost.

**Cost:** about $1.25 and 2-3 minutes per question with the defaults (4 sub-questions × 3 searches). Research is about half the cost, because each search reads ~17-20k tokens of pages.

## Layout

```
research.py              CLI
research_agent/
  config.py              models, budgets, prices
  llm.py                 API calls + the cost ledger (every call is measured)
  planner.py             [1] query decomposition
  researcher.py          [2] parallel web-search workers + citation extraction
  validator.py           [3] claim-vs-quote validation
  evidence.py            sources, dedupe, context budget, compression
  synthesizer.py         [4] the brief + citation audit
  agent.py               the pipeline
  report.py              brief markdown, cost table, trace
tests/test_offline.py    deterministic steps, no API calls
```

## License

MIT
