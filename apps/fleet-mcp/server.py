"""The fleet's MCP server: every agent in the fleet, exposed as tools for Mother (or any MCP client).

Each agent is a standalone app with its own venv and a CLI that follows the fleet's result
contract: with --json it prints progress on stderr and exactly one JSON object on stdout,
{agent, status: "ok"|"error", output, error, cost_usd, seconds, artifacts}. This server only
runs that CLI and relays the result, so agents never share dependencies and each ships on its own.
New agent = one more tool here + one line in Mother's tool list.

stdout belongs to the MCP protocol (JSON-RPC over stdio): nothing in this file may print to it.

    .venv/bin/python server.py          # normally started by Claude Code from .mcp.json
"""

import asyncio
import json
import re
from collections import deque
from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError

APPS = Path(__file__).resolve().parent.parent
RESEARCH = APPS / "research-agent"
RUN_TIMEOUT = 15 * 60          # seconds; a normal research run takes 2-3 minutes
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
        raise ToolError(f"{result.get('error') or 'failed'} (spent ${result.get('cost_usd', 0):.2f})")
    return result


def as_text(result: dict) -> str:
    """The result as an LLM reads it best: a one-line header, where the files are, then the output."""
    output = result.get("output") or ""
    files = result.get("artifacts") or {}
    if len(output) > MAX_OUTPUT_CHARS:
        output = output[:MAX_OUTPUT_CHARS] + f"\n\n[cut at {MAX_OUTPUT_CHARS:,} characters; the full text is in {files.get('brief', 'the saved file')}]"
    header = f"{result.get('agent')} · ${result.get('cost_usd', 0):.2f} · {result.get('seconds', 0):.0f}s"
    return "\n".join([header] + [f"{name}: {path}" for name, path in files.items()] + ["", output])


@mcp.tool()
async def research(question: str, ctx: Context, sub_questions: int = MAX_SUB_QUESTIONS,
                   searches: int = MAX_SEARCHES) -> str:
    """Research a question on the web and return a brief in which every sentence cites its source.

    Costs real money and time: about $1.25 and 2-3 minutes with the defaults (4 sub-questions,
    3 searches each). sub_questions=2, searches=1 is about $0.35 and fine for narrow questions.
    Call recent_research first: an existing brief may already answer the question for free."""
    sub_questions = max(1, min(sub_questions, MAX_SUB_QUESTIONS))
    searches = max(1, min(searches, MAX_SEARCHES))
    result = await run_agent(RESEARCH, ["research.py", "--json", "--sub-questions", str(sub_questions),
                                        "--searches", str(searches), question], ctx)
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
