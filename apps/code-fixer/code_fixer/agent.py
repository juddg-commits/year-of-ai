"""The fix loop. Copy the repo, confirm its tests fail, let the model investigate and edit with
tools until it stops or a ceiling is hit, then judge the result with a fresh sandbox run.

The model's word is never the verdict: "fixed" means the final check passed, with the test
files restored from the pristine copy and every test that ran before now passing.

The conversation is append-only: the system prompt, the tool list and every earlier message
stay byte-identical from the first call to the last. Opus 5.5 ties its thinking to that exact
prefix, and the prompt cache depends on it too. Notes to the model (a limit getting close)
travel inside tool results, never as edits to earlier turns."""

import json
import shlex
import shutil
import tempfile
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from . import config, llm, sandbox, workspace
from .tools import TOOLS, ToolBox, ToolError

SYSTEM = """You fix bugs in Python repositories. The repo's tests fail. Find the cause and fix the code so that every test passes.

- Start from the failing output, then read the code it points to. Find the root cause before you edit.
- Fix the code, never the tests: test files and test config are read-only. A change that only satisfies these particular tests (special-casing their inputs, catching the error, loosening a check) is wrong even when they pass.
- Keep the change as small as the bug allows, in the style of the surrounding code.
- After editing, run the tests. When they all pass, stop and reply in two or three sentences: what was wrong and what you changed.
- If you run out of budget or can't find the cause, stop and say what you found."""
SYSTEM_BLOCKS = [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}]

# The eval's baseline without the test loop: the same prompt, tools and limits, minus run_tests.
# The model gets one patch, checked once after it stops. Measures what running the tests buys.
_RUN_LINE = ("- After editing, run the tests. When they all pass, stop and reply in two or three sentences: "
             "what was wrong and what you changed.")
_NO_RUN_LINE = ("- You can't run the tests: your fix is checked once, after you stop. Read until you're sure of "
                "the cause, make the edit, then stop and reply in two or three sentences: what was wrong and what "
                "you changed.")
assert _RUN_LINE in SYSTEM
SYSTEM_NO_TESTS = SYSTEM.replace(_RUN_LINE, _NO_RUN_LINE)
SYSTEM_NO_TESTS_BLOCKS = [{"type": "text", "text": SYSTEM_NO_TESTS, "cache_control": {"type": "ephemeral"}}]
TOOLS_NO_TESTS = [t for t in TOOLS if t["name"] != "run_tests"]

MAX_FILES_LISTED = 200
TRACE_TEXT = 2_000   # characters of each tool result / thinking summary kept in the trace

STOPS = {   # why the loop ended, in words for the output
    "done": "the model finished",
    "nothing_to_fix": "the tests already pass",
    "budget": "the dollar ceiling was reached",
    "max_tool_calls": "the tool-call limit was reached",
    "max_turns": "the turn limit was reached",
    "timeout": "the time limit was reached",
    "refused": "the model declined the request",
    "output_limit": "a reply hit the output limit",
    "api_error": "an API call failed",
    "sandbox_error": "the sandbox failed",
    "bad_command": "the test command ran no tests",
    "crash": "the fixer hit an internal error",
    "interrupted": "the run was interrupted",
}


@dataclass
class Limits:
    max_usd: float = config.MAX_USD
    max_tool_calls: int = config.MAX_TOOL_CALLS
    max_test_runs: int = config.MAX_TEST_RUNS
    max_turns: int = config.MAX_TURNS
    max_seconds: float = config.MAX_SECONDS


@dataclass
class FixResult:
    repo: str
    command: list
    model: str
    fixed: bool = False
    stop: str = ""                   # a key of STOPS
    summary: str = ""                # the model's closing words
    diff: str = ""
    changed: dict = field(default_factory=dict)      # relative path -> new text (the eval overlays these)
    before: sandbox.TestRun | None = None
    after: sandbox.TestRun | None = None
    unresolved: list = field(default_factory=list)   # tests that ran before and don't pass after
    cost: float = 0.0
    seconds: float = 0.0
    turns: int = 0
    tool_calls: int = 0
    test_runs: int = 0
    error: str | None = None
    trace: dict = field(default_factory=dict)


def task_message(repo_name: str, command: list, before: sandbox.TestRun, files: list, limits: Limits,
                 test_loop: bool = True) -> str:
    listed = [f + (" [read-only]" if workspace.is_protected(f) else "") for f in files[:MAX_FILES_LISTED]]
    if len(files) > MAX_FILES_LISTED:
        listed.append(f"[… {len(files) - MAX_FILES_LISTED} more: use list_files]")
    budget = (f"{limits.max_test_runs} test runs and {limits.max_tool_calls} tool calls." if test_loop else
              f"{limits.max_tool_calls} tool calls, and no test runs.")
    return (f"Repository: {repo_name}\nTest command: {shlex.join(command)}\n\n"
            f"The tests fail ({before.summary()}). Output:\n```\n{before.output}\n```\n\n"
            f"Files:\n" + "\n".join(listed) + "\n\n"
            f"Budget for this fix: {budget}")


def unresolved_tests(before: sandbox.TestRun, after: sandbox.TestRun) -> list:
    """Every test that passed, failed or errored before must pass after. A missing test counts as
    unresolved, so emptying a parametrize list can't pass for a fix. A file-level collection error
    before is resolved when that file's tests all run and pass after."""
    out = []
    for test, outcome in before.outcomes.items():
        if outcome not in ("passed", "failed", "error"):
            continue
        if after.outcomes.get(test) == "passed":
            continue
        if "::" not in test and after.passed and any(k.startswith(test + "::") for k in after.outcomes):
            continue
        out.append(test)
    return out


def _clip(text: str, limit: int = TRACE_TEXT) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + f" [… {len(text) - limit:,} more chars]"


def _clip_input(value):
    if isinstance(value, dict):
        return {k: _clip_input(v) for k, v in value.items()}
    return _clip(value) if isinstance(value, str) else value


def fix(repo, command: list, *, limits: Limits | None = None, model: str = config.MODEL,
        effort: str = config.EFFORT, client=None, runner: Callable = sandbox.run_tests,
        log: Callable = lambda msg: None, test_loop: bool = True) -> FixResult:
    """Try to fix the repo at `repo` so `command` passes. Never modifies `repo`.
    test_loop=False is the eval's baseline: no run_tests tool, one patch, checked once."""
    limits = limits or Limits()
    repo = Path(repo).resolve()
    if not repo.is_dir():
        raise ValueError(f"{repo} is not a directory")
    started = time.monotonic()
    ledger = llm.Ledger()
    res = FixResult(repo=str(repo), command=list(command), model=model)
    res.trace = {"repo": str(repo), "command": list(command), "model": model, "effort": effort,
                 "test_loop": test_loop,
                 "limits": asdict(limits), "image": sandbox.image_id() if runner is sandbox.run_tests else None,
                 "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "turns": [], "refused_edits": []}

    with tempfile.TemporaryDirectory(prefix="code-fixer-") as tmp:
        pristine, work, check = Path(tmp) / "pristine", Path(tmp) / "work", Path(tmp) / "check"
        copied = workspace.copy_repo(repo, pristine)
        shutil.copytree(pristine, work)
        res.trace["files"] = len(copied.files)
        res.trace["skipped_files"] = copied.skipped

        log("[1/3] Running the tests in the sandbox…")
        res.before = runner(work, command)
        res.trace["before"] = res.before.as_dict()
        log(f"      {res.before.summary()}")
        if res.before.error:
            return _finish(res, ledger, started, "sandbox_error", error=res.before.error)
        if res.before.passed:
            return _finish(res, ledger, started, "nothing_to_fix")
        if sandbox.is_pytest(command) and res.before.exit_code in (4, 5) and not res.before.outcomes:
            # pytest's "usage error" and "no tests collected": nothing a code fix can change, so no API call
            return _finish(res, ledger, started, "bad_command",
                           error=f"pytest exit {res.before.exit_code}: " + res.before.output.strip()[-300:])

        box = ToolBox(work, copied.files, list(command), runner, limits.max_test_runs if test_loop else 0)
        messages = [{"role": "user", "content": task_message(repo.name, command, res.before, copied.files, limits,
                                                             test_loop)}]
        log(f"[2/3] Fixing with {model} (effort {effort}, up to ${limits.max_usd:.2f})"
            + ("" if test_loop else ", no test runs") + "…")
        try:
            client = client or llm.make_client()
            system, tools = (SYSTEM_BLOCKS, TOOLS) if test_loop else (SYSTEM_NO_TESTS_BLOCKS, TOOLS_NO_TESTS)
            stop, summary = _loop(client, ledger, box, messages, model, effort, limits, started, res, log,
                                  system, tools)
        except KeyboardInterrupt:
            stop, summary = "interrupted", ""
        except Exception as e:   # a paid run must not crash: keep what was done, check it, save the trace
            stop, summary = "crash", ""
            res.error = f"{type(e).__name__}: {e}"
            res.trace["crash"] = traceback.format_exc()
        res.summary = summary
        res.test_runs = len(box.test_runs)
        res.trace["refused_edits"] = box.refused

        log("[3/3] Checking the result in a fresh sandbox…")
        try:
            shutil.copytree(work, check)
            tampered = workspace.restore_protected(pristine, check, copied.files)
            if tampered:
                res.trace["protected_files_changed"] = tampered   # the tools refuse these edits: should never happen
            res.diff = workspace.make_diff(pristine, check, copied.files)
            res.changed = {rel: (check / rel).read_bytes().decode("utf-8", errors="replace")
                           for rel in workspace.changed_files(pristine, check, copied.files)}
            if stop != "interrupted":
                res.after = runner(check, command)
                res.trace["after"] = res.after.as_dict()
                res.unresolved = unresolved_tests(res.before, res.after)
                res.fixed = res.after.passed and not res.unresolved and bool(res.diff)
                log(f"      {res.after.summary()}" + (f"; unresolved: {', '.join(res.unresolved[:5])}" if res.unresolved else ""))
        except Exception as e:
            res.error = res.error or f"final check failed: {type(e).__name__}: {e}"
            res.trace["check_crash"] = traceback.format_exc()
        return _finish(res, ledger, started, stop)


def _finish(res: FixResult, ledger: llm.Ledger, started: float, stop: str, error: str | None = None) -> FixResult:
    res.stop = stop
    res.error = res.error or error
    res.cost = ledger.total()
    res.seconds = time.monotonic() - started
    res.trace.update({"stop": stop, "stop_text": STOPS.get(stop, stop), "fixed": res.fixed, "error": res.error,
                      "summary": res.summary, "diff": res.diff, "unresolved": res.unresolved,
                      "cost_usd": round(res.cost, 4), "seconds": round(res.seconds, 1),
                      "calls": [asdict(c) for c in ledger.calls], "tool_calls": res.tool_calls,
                      "test_runs": res.test_runs, "turns_used": res.turns})
    return res


def _loop(client, ledger: llm.Ledger, box: ToolBox, messages: list, model: str, effort: str,
          limits: Limits, started: float, res: FixResult, log: Callable,
          system: list = SYSTEM_BLOCKS, tools: list = TOOLS) -> tuple:
    """Returns (stop reason, the model's closing text)."""
    cached, new = 0, llm.estimate_tokens(len(system[0]["text"]) + len(json.dumps(tools)) + len(messages[0]["content"]))
    cache_hit = True
    for turn in range(1, limits.max_turns + 1):
        if time.monotonic() - started > limits.max_seconds:
            return "timeout", ""
        max_tokens = min(config.MAX_TOKENS, llm.affordable_max_tokens(
            model, limits.max_usd - ledger.total(), cached, new, cache_hit))
        if max_tokens < config.MIN_TOKENS:
            return "budget", ""
        # The time limit is checked between calls, so it has to bound each call too: one stalled
        # stream once ran past 30 minutes through the SDK's retries. Split what's left across them.
        left = limits.max_seconds - (time.monotonic() - started)
        call_timeout = max(config.MIN_CALL_TIMEOUT, left / (config.API_RETRIES + 1))
        t0 = time.monotonic()
        try:
            response = llm.call(client, model=model, effort=effort, system=system, tools=tools,
                                messages=messages, max_tokens=max_tokens, timeout=call_timeout)
        except Exception as e:   # after the SDK's own retries. Log what failed: a hidden error hides the bug
            status = getattr(e, "status_code", None)
            res.error = f"{type(e).__name__}" + (f" (HTTP {status})" if status else "") + f": {e}"
            res.trace["api_error"] = {"turn": turn, "type": type(e).__name__, "status": status, "message": str(e)[:2000]}
            log(f"      API call failed: {res.error[:200]}")
            return "api_error", ""
        rec = ledger.record(turn, model, response, time.monotonic() - t0, max_tokens)
        res.turns = turn
        content = llm.echo_content(response.content)
        step = {"turn": turn, "cost": round(rec.cost, 4), "stop_reason": rec.stop_reason, "model": rec.model,
                "fallback": rec.fallback,
                "thinking": [_clip(b.thinking) for b in content if b.type == "thinking" and b.thinking],
                "text": _clip("\n".join(b.text for b in content if b.type == "text")), "tools": []}
        res.trace["turns"].append(step)
        if response.stop_reason == "refusal":
            return "refused", ""
        messages.append({"role": "assistant", "content": content})
        calls = [b for b in content if b.type == "tool_use"]
        text = "\n".join(b.text for b in content if b.type == "text").strip()
        if not calls:
            return ("output_limit" if response.stop_reason == "max_tokens" else "done"), text

        results = []
        for block in calls:
            if response.stop_reason == "max_tokens":   # cut off mid-call: the input may be incomplete
                out, is_error = "Not run: your reply hit the output limit before this call was complete. Send it again.", True
                step["tools"].append({"name": block.name, "not_run": "output limit"})
            elif res.tool_calls >= limits.max_tool_calls:
                out, is_error = "Not run: the tool-call limit is used up.", True
                step["tools"].append({"name": block.name, "not_run": "tool-call limit"})
            else:
                res.tool_calls += 1
                t1 = time.monotonic()
                try:
                    out, is_error = box.call(block.name, block.input), False
                except ToolError as e:
                    out, is_error = f"Error: {e}", True
                except Exception as e:   # a bug in a tool: the model is told, the trace keeps the details
                    out, is_error = f"Error: the tool failed ({type(e).__name__}: {e})", True
                    step["tool_crash"] = traceback.format_exc()
                step["tools"].append({"name": block.name, "input": _clip_input(block.input), "error": is_error,
                                      "seconds": round(time.monotonic() - t1, 2), "result": _clip(out)})
                log(f"      {turn:>2}. {block.name}{_brief(block.name, block.input)}" + ("  ✗" if is_error else ""))
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": out, "is_error": is_error})
        left = limits.max_tool_calls - res.tool_calls
        if 0 < left <= 5:   # a note inside the last result: appending keeps the conversation's prefix intact
            results[-1]["content"] += f"\n\n[{left} tool call{'s' if left > 1 else ''} left.]"
        messages.append({"role": "user", "content": results})
        if res.tool_calls >= limits.max_tool_calls:
            return "max_tool_calls", text
        if box.test_runs and box.test_runs[-1].error:
            return "sandbox_error", text

        cached = rec.prompt_tokens
        new = rec.output_tokens + llm.estimate_tokens(sum(len(r["content"]) for r in results))
        cache_hit = (rec.cache_read + rec.cache_write) > 0
    return "max_turns", ""


def _brief(name: str, args) -> str:
    if not isinstance(args, dict):
        return ""
    if name in ("read_file", "edit_file", "list_files") and args.get("path"):
        return f" {args['path']}"
    if name == "search":
        return f" {args.get('pattern', '')!r}"
    return ""
