# Ship #2 write-up: research agent (draft, Judd edits before posting)

_What it is, one line:_ ask a hard question, get a brief where every sentence cites its source. Opus 5 plans the research and checks the evidence, Sonnet 5 researches the sub-questions in parallel with web search, and plain code keeps everyone honest. Built with Claude Code as my pair programmer.

## What broke
- **The first run found zero evidence.** The newest version of the web search tool routes results through a code sandbox, and its answers came back with no citation objects at all. I only saw it by dumping the raw API response for one worker. The older search tool returned 18 cited claims in 29 seconds for the same question. For an agent whose whole point is provenance, I pinned the older tool.
- **Most claims were only "partly" supported, and the fix I'd planned was the wrong one.** The plan was to judge each claim against all of its quotes at once. Before building it, I counted why claims failed: 76-88% of the time, the API had cut the quote off at 150 characters, often right before the number the claim was about. So the agent now downloads the source page and completes the sentence, in plain code with no extra tokens. On the same evidence, "partial" went from 64% to 41%.
- **My first run cost $1.68 and took 6 minutes 40 seconds.** A per-stage cost ledger showed the validator was burning money on thinking tokens for what is really a classification task. Lower effort and parallel batches brought a run to $1.25 and 2.5 minutes.

## What I learned
- **Measure before you build.** Twice, the obvious fix wasn't where the problem was: the cost was in validation, not research, and the "partial" verdicts came from cut-off quotes, not multi-quote claims.
- **A workflow isn't a weaker agent.** The order of steps is fixed in code and the model decides inside each step. That makes cost and failures predictable and every stage testable.
- **An LLM judge isn't independent per item.** 10 of 48 claims whose evidence didn't change still got a different verdict, all stricter, when their neighbors in the batch got better quotes. One run's small differences are noise.
- **Uncited text never reaches the reader.** 45 of 46 sentences cited, and zero citations to sources that don't exist, checked by code, not by trust.

## What's next
- It becomes the first member of a fleet: a Mother orchestrator that calls each of my agents as a tool through an MCP server. Next ship: an agent that fixes code and proves it by running the tests in a sandbox.

Repo: https://github.com/juddg-commits/year-of-ai/tree/main/apps/research-agent
