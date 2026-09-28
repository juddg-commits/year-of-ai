"""Every knob in one place: which model does what, the budgets, and the prices."""

from pathlib import Path

# Orchestrator-worker split: judgment-heavy steps with few calls get the strong
# model; the token-heavy parallel research gets the cheaper one.
ORCHESTRATOR_MODEL = "claude-opus-5"    # plan, validate, write the brief
WORKER_MODEL = "claude-sonnet-5"        # web research (one worker per sub-question)

MAX_SUB_QUESTIONS = 4
SEARCHES_PER_WORKER = 3          # web_search max_uses. One search ≈ 20k input tokens: THE cost driver
MAX_PARALLEL_WORKERS = 4
MAX_PAUSE_CONTINUATIONS = 3      # server-side tool loops can stop with pause_turn; resume at most this often
MAX_PAGE_FETCHES = 12            # parallel page downloads to recover cut-off quotes (plain HTTP, no tokens)
PAGE_FETCH_TIMEOUT = 10          # seconds per page; a slow page just keeps its API quote
VALIDATION_BATCH = 40            # evidence items per validator call
EVIDENCE_TOKEN_BUDGET = 20_000   # above this, compress validated evidence before synthesis (typical run: ~14k)

# $ per million tokens: (input, output). Cache writes cost 1.25x input, reads 0.1x.
PRICES = {
    "claude-opus-5": (5.00, 25.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-4-8": (5.00, 25.00),   # where a refused Opus request can fall back to
}
CACHE_WRITE_MULT, CACHE_READ_MULT = 1.25, 0.10
WEB_SEARCH_PRICE = 10.00 / 1000          # $10 per 1,000 searches

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "runs"
