"""Every knob in one place: the model, the ceilings, the sandbox, and the prices."""

from pathlib import Path

MODEL = "claude-opus-5-5"   # Judd's pick (2026-09-30); Sonnet 5 gets one comparison run
EFFORT = "medium"           # Opus 5.5's default. Effort is its only thinking control: tune by measurement
MAX_TOKENS = 32_000         # per response, thinking included; lowered when the budget can't cover it
MIN_TOKENS = 3_000          # a call the budget can't give this much output isn't worth making

# Ceilings for one fix. The dollar ceiling is checked before every call (llm.affordable_max_tokens).
MAX_USD = 0.50
MAX_TOOL_CALLS = 40
MAX_TEST_RUNS = 8           # the model's runs; the final check is the checker's and doesn't count
MAX_TURNS = 30
MAX_SECONDS = 15 * 60

# What the model sees of long text
READ_MAX_LINES = 400        # per read_file call
SEARCH_MAX_MATCHES = 100
TEST_OUTPUT_HEAD, TEST_OUTPUT_TAIL = 2_000, 8_000   # characters; pytest puts the failures at the end

# Sandbox: every test run is a fresh container (see sandbox.py for the flags)
IMAGE = "code-fixer-sandbox:1"
TEST_TIMEOUT = 60           # seconds per test run
DEFAULT_TEST_COMMAND = "python -m pytest -q"

# Which repos we copy
MAX_REPO_FILES = 3_000
MAX_REPO_BYTES = 30_000_000
MAX_FILE_BYTES = 5_000_000

# $ per million tokens: (input, output, 5-minute cache write, cache read).
# Cache reads are 0.05x input on Opus 5.5 and 0.1x elsewhere; writes are 1.25x.
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 5.00, 0.20),
    "claude-opus-5": (5.00, 25.00, 6.25, 0.50),
    "claude-opus-4-8": (5.00, 25.00, 6.25, 0.50),   # where a refused Opus request can fall back to
    "claude-sonnet-5": (2.00, 10.00, 2.50, 0.20),
}
# Opus requests opt into server-side refusal fallback (a rare classifier decline is re-run
# on the model Anthropic recommends for it instead of ending the fix).
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5"}

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = ROOT / "runs"
EVALS_DIR = ROOT / "evals"
