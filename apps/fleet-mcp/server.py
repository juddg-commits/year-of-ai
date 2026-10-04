"""The fleet's MCP server: every agent in the fleet, exposed as tools for Mother (or any MCP client).

Each agent is a standalone app with its own venv and a CLI that follows the fleet's result
contract: with --json it prints progress on stderr and exactly one JSON object on stdout,
{agent, status: "ok"|"error", output, error, cost_usd, seconds, artifacts}. This server only
runs that CLI and relays the result, so agents never share dependencies and each ships on its own.
New agent = one more tool here + one line in Mother's tool list.

Tools: research and recent_research (the research agent), fix_code (the code fixer).

stdout belongs to the MCP protocol (JSON-RPC over stdio): nothing in this file may print to it.

    .venv/bin/python server.py          # normally started by Claude Code from .mcp.json
"""

import asyncio
import json
import math
import re
from collections import deque
from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError

APPS = Path(__file__).resolve().parent.parent
RESEARCH = APPS / "research-agent"
CODE_FIXER = APPS / "code-fixer"
RUN_TIMEOUT = 15 * 60          # seconds; the saved research runs took 1.2 to 7.5 minutes
FIX_TIMEOUT = 20 * 60          # the fixer stops itself at 15 minutes, plus a sandbox run before and after
MAX_FIX_USD = 0.50             # the code fixer's ceiling: a caller can ask for less, never more
MAX_OUTPUT_CHARS = 40_000      # Claude Code caps a tool result at ~25k tokens; past this, read the file
MAX_SUB_QUESTIONS, MAX_SEARCHES = 4, 3   # the cost ceiling: a caller can ask for less, never more
STAGE = re.compile(r"^\[(\d+)/(\d+)\]")   # progress lines look like "[3/6] Recovering…"

mcp = FastMCP("fleet")


async def run_agent(app: Path, args: list, ctx: Context | None = None, *,
                    python: str | None = None, timeout: float = RUN_TIMEOUT) -> dict:
    """Run an agent's CLI in its own venv and return its JSON result. Progress lines are
    relayed to the client as they happen. Raises ToolError on any failure, including the
    agent reporting status "error"."""
    python = python or str(app / ".venv" / "bin" / "python")
    if not Path(python).exists():
        raise ToolError(f"{app.name} isn't installed: create {app.name}/.venv (see its README)")
    proc = await asyncio.create_subprocess_exec(
        python, *args, cwd=app, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    tail = deque(maxlen=15)     # the last progress lines, for error messages

    async def relay_progress():
        async for raw in proc.stderr:
            line = raw.decode(errors="replace").rstrip()
            if not line:
                continue
            tail.append(line.strip())
            stage = STAGE.match(line)
            if ctx and stage:
                await ctx.report_progress(int(stage[1]) - 1, int(stage[2]), message=line)

    try:
        out, _, code = await asyncio.wait_for(
            asyncio.gather(proc.stdout.read(), relay_progress(), proc.wait()), timeout)
    except asyncio.TimeoutError:
        raise ToolError(f"{app.name} ran past {timeout:.0f}s and was stopped") from None
    finally:
        if proc.returncode is None:   # timed out, cancelled or failed: a stray run must stop spending money
            proc.kill()
            await proc.wait()

    lines = out.decode(errors="replace").strip().splitlines()
    try:
        result = json.loads(lines[-1])
    except (IndexError, json.JSONDecodeError):
        raise ToolError(f"{app.name} exited {code} without a result. Last output:\n" + "\n".join(tail)) from None
    if result.get("status") != "ok":
        trace = (result.get("artifacts") or {}).get("trace")
        raise ToolError(f"{result.get('error') or 'failed'} (spent ${result.get('cost_usd', 0):.2f})"
                        + (f"\ntrace: {trace}" if trace else ""))
    return result


def as_text(result: dict) -> str:
    """The result as an LLM reads it best: a one-line header, where the files are, then the output."""
    output = result.get("output") or ""
    files = result.get("artifacts") or {}
    if len(output) > MAX_OUTPUT_CHARS:
        saved = files.get("brief") or files.get("patch") or "the saved file"
        output = output[:MAX_OUTPUT_CHARS] + f"\n\n[cut at {MAX_OUTPUT_CHARS:,} characters; the full text is in {saved}]"
    header = f"{result.get('agent')} · ${result.get('cost_usd', 0):.2f} · {result.get('seconds', 0):.0f}s"
    return "\n".join([header] + [f"{name}: {path}" for name, path in files.items()] + ["", output])


@mcp.tool()
async def research(question: str, ctx: Context, sub_questions: int = MAX_SUB_QUESTIONS,
                   searches: int = MAX_SEARCHES) -> str:
    """Research a question on the web and return a brief in which every sentence cites its source.

    Costs real money and time: the tuned run cost $1.25 and took 2.5 minutes with the defaults
    (4 sub-questions, 3 searches each; before quote recovery). On the shipped code, sub_questions=2,
    searches=1 cost $0.37 and took 7.5 minutes, mostly waiting on web search; fine for narrow questions.
    Call recent_research first: an existing brief may already answer the question for free."""
    sub_questions = max(1, min(sub_questions, MAX_SUB_QUESTIONS))
    searches = max(1, min(searches, MAX_SEARCHES))
    result = await run_agent(RESEARCH, ["research.py", "--json", "--sub-questions", str(sub_questions),
                                        "--searches", str(searches), question], ctx)
    return as_text(result)


@mcp.tool()
async def fix_code(repo_path: str, ctx: Context, test_command: str = "python -m pytest -q",
                   max_usd: float = MAX_FIX_USD) -> str:
    """Fix a small Python repo whose tests fail, and return a patch checked in a fresh sandbox run.

    Never edits the repo: it works on a copy and returns a patch to review and apply (git apply),
    the before/after test results and the trace. Python with pytest-compatible tests only; the
    sandbox has Python 3.12 and pytest and no network, so tests that need other packages can't run.
    Costs real money: about $0.06 a fix on the dev eval, at most max_usd ($0.50 is the ceiling).
    Fails without spending if the tests already pass or the command collects no tests."""
    if not isinstance(max_usd, (int, float)) or not math.isfinite(max_usd) or max_usd <= 0:
        raise ToolError("max_usd must be a positive number of dollars")
    max_usd = min(max(max_usd, 0.01), MAX_FIX_USD)
    repo = Path(repo_path).expanduser()
    if not repo.is_absolute():
        raise ToolError("repo_path must be an absolute path")
    if not repo.is_dir():
        raise ToolError(f"{repo} is not a directory")
    result = await run_agent(CODE_FIXER, ["fix.py", "--json", "--test", test_command, "--max-usd", f"{max_usd:.2f}",
                                          str(repo.resolve())], ctx, timeout=FIX_TIMEOUT)
    return as_text(result)


@mcp.tool()
def recent_research(limit: int = 10) -> str:
    """List past research briefs, newest first: date, cost, question, and the brief's file path.
    Free. Read a brief's file to reuse it instead of paying for a new run."""
    return list_runs(RESEARCH / "runs", limit)


def list_runs(runs_dir: Path, limit: int) -> str:
    rows = []
    for trace in sorted(runs_dir.glob("*.json"), reverse=True):   # names start with the timestamp
        if trace.name.endswith("-FAILED.json"):
            continue
        try:
            t = json.loads(trace.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        day = f"{trace.name[:4]}-{trace.name[4:6]}-{trace.name[6:8]}"
        rows.append(f"- {day} · ${t.get('total_cost', 0):.2f} · {t.get('question', '?')}\n  brief: {trace.with_suffix('.md')}")
        if len(rows) >= limit:
            break
    return "\n".join(rows) or "No research briefs yet."


if __name__ == "__main__":
    mcp.run()
