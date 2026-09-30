"""The five tools the model works with, and the guards behind them.

Paths are relative to the repo root and checked: nothing outside the workspace can be read or
written. Test files and pytest's config can be read but never edited; a refused edit is logged,
and the final check restores those files from the pristine copy anyway. There is no tool to
create or delete files: a fix edits existing code."""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import config, workspace

TOOLS = [
    {
        "name": "list_files",
        "description": "List the repo's files with their sizes. Files marked [read-only] are tests or "
                       "test config: read them, don't edit them.",
        "input_schema": {"type": "object", "additionalProperties": False, "properties": {
            "path": {"type": "string", "description": "A directory relative to the repo root. Default: the whole repo."}}},
    },
    {
        "name": "read_file",
        "description": f"Read a text file, with line numbers. Returns at most {config.READ_MAX_LINES} lines "
                       "per call; use start_line and end_line to read the rest of a long file.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["path"], "properties": {
            "path": {"type": "string", "description": "File path relative to the repo root."},
            "start_line": {"type": "integer", "description": "First line to return (1-based). Default 1."},
            "end_line": {"type": "integer", "description": "Last line to return (inclusive)."}}},
    },
    {
        "name": "search",
        "description": f"Search the repo's text files for a Python regular expression. Returns up to "
                       f"{config.SEARCH_MAX_MATCHES} matching lines as path:line: text.",
        "input_schema": {"type": "object", "additionalProperties": False, "required": ["pattern"], "properties": {
            "pattern": {"type": "string", "description": "A Python regular expression (re module syntax)."},
            "path": {"type": "string", "description": "Only search this file or directory."}}},
    },
    {
        "name": "edit_file",
        "description": "Replace an exact string in a file. old_string must match the file exactly, whitespace "
                       "and indentation included, and appear exactly once (include nearby lines to make it "
                       "unique) unless replace_all is true. Tests and test config are read-only: fix the code, "
                       "not the tests.",
        "input_schema": {"type": "object", "additionalProperties": False,
                         "required": ["path", "old_string", "new_string"], "properties": {
            "path": {"type": "string", "description": "File path relative to the repo root."},
            "old_string": {"type": "string", "description": "The exact text to replace."},
            "new_string": {"type": "string", "description": "The text to put in its place."},
            "replace_all": {"type": "boolean", "description": "Replace every occurrence. Default false."}}},
    },
    {
        "name": "run_tests",
        "description": "Run the repo's test command in the sandbox and return the result. Test runs are "
                       "limited (each result says how many are left): read and reason first, then run the "
                       "tests to check a fix.",
        "input_schema": {"type": "object", "additionalProperties": False, "properties": {}},
    },
]
SCHEMAS = {t["name"]: t["input_schema"] for t in TOOLS}
TYPES = {"string": str, "integer": int, "boolean": bool}
MAX_LINE_CHARS = 2_000
MAX_LISTED = 500


class ToolError(Exception):
    """A bad call the model can correct: the message goes back to it as an error result."""


def check_args(name: str, args) -> None:
    schema = SCHEMAS[name]
    if not isinstance(args, dict):
        raise ToolError("the input must be a JSON object")
    missing = [k for k in schema.get("required", []) if k not in args]
    extra = [k for k in args if k not in schema["properties"]]
    if missing or extra:
        raise ToolError(f"bad arguments for {name}: " + "; ".join(
            ([f"missing {', '.join(missing)}"] if missing else []) + ([f"unknown {', '.join(extra)}"] if extra else [])))
    for key, value in args.items():
        kind = schema["properties"][key]["type"]
        want = TYPES[kind]
        if not isinstance(value, want) or (want is int and isinstance(value, bool)):
            raise ToolError(f"{key} must be {'an' if kind[0] in 'aeiou' else 'a'} {kind}")


@dataclass
class ToolBox:
    root: Path                 # the workspace
    files: list                # its files (relative paths), as copied
    command: list              # the test command, argv form
    runner: Callable           # (workspace, command) -> sandbox.TestRun
    max_test_runs: int = config.MAX_TEST_RUNS
    test_runs: list = field(default_factory=list)   # a TestRun per run_tests call
    refused: list = field(default_factory=list)     # guard events, for the trace
    edits: int = 0

    def call(self, name: str, args) -> str:
        """Run one tool. Raises ToolError for a call the model should correct."""
        if name not in SCHEMAS:
            raise ToolError(f"unknown tool {name!r}; the tools are {', '.join(SCHEMAS)}")
        check_args(name, args)
        return getattr(self, name)(**args)

    # --- helpers ---
    def _path(self, path: str) -> tuple:
        try:
            return workspace.resolve_inside(self.root, path)
        except ValueError as e:
            self.refused.append({"path": path, "why": str(e)})
            raise ToolError(str(e)) from None

    def _text(self, path: str) -> tuple:
        full, rel = self._path(path)
        if not full.is_file():
            raise ToolError(f"{rel} is not a file" + (" (it's a directory)" if full.is_dir() else ""))
        try:
            return full, rel, full.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            raise ToolError(f"{rel} is not a UTF-8 text file") from None

    def _under(self, path: str | None) -> list:
        if path in (None, "", "."):
            return list(self.files)
        full, rel = self._path(path)
        chosen = [f for f in self.files if f == rel or f.startswith(rel + "/")]
        if not chosen:
            raise ToolError(f"no files at {rel}")
        return chosen

    @staticmethod
    def _numbered(lines: list, start: int) -> str:
        out = []
        for n, line in enumerate(lines, start):
            line = line.rstrip("\r")
            if len(line) > MAX_LINE_CHARS:
                line = line[:MAX_LINE_CHARS] + " [… line cut]"
            out.append(f"{n:>5}\t{line}")
        return "\n".join(out)

    # --- the tools ---
    def list_files(self, path: str = ".") -> str:
        chosen = self._under(path)
        rows = [f"{rel} ({(self.root / rel).stat().st_size:,} bytes)" + (" [read-only]" if workspace.is_protected(rel) else "")
                for rel in chosen[:MAX_LISTED]]
        if len(chosen) > MAX_LISTED:
            rows.append(f"[… {len(chosen) - MAX_LISTED} more: list a subdirectory]")
        return "\n".join(rows)

    def read_file(self, path: str, start_line: int = 1, end_line: int | None = None) -> str:
        _, rel, text = self._text(path)
        lines = text.split("\n")
        if lines[-1] == "":
            lines.pop()
        total = len(lines)
        if total == 0:
            return f"{rel} is empty."
        start = max(1, start_line)
        if start > total:
            raise ToolError(f"{rel} has only {total} lines")
        end = min(total, end_line if end_line is not None else total, start + config.READ_MAX_LINES - 1)
        if end < start:
            raise ToolError("end_line is before start_line")
        more = f"\n[{total - end} more lines: read_file with start_line={end + 1}]" if end < total else ""
        return f"{rel}, lines {start}-{end} of {total}:\n" + self._numbered(lines[start - 1:end], start) + more

    def search(self, pattern: str, path: str | None = None) -> str:
        try:
            rx = re.compile(pattern)
        except re.error as e:
            raise ToolError(f"bad regular expression ({e}); escape special characters like ( [ . *") from None
        matches, capped = [], False
        for rel in self._under(path):
            try:
                text = (self.root / rel).read_bytes().decode("utf-8")
            except UnicodeDecodeError:
                continue
            for n, line in enumerate(text.split("\n"), 1):
                if rx.search(line):
                    if len(matches) == config.SEARCH_MAX_MATCHES:
                        capped = True
                        break
                    matches.append(f"{rel}:{n}: {line.strip()[:300]}")
            if capped:
                break
        if not matches:
            return "No matches."
        return "\n".join(matches) + (f"\n[stopped at {config.SEARCH_MAX_MATCHES} matches: narrow the pattern or path]" if capped else "")

    def edit_file(self, path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
        full, rel = self._path(path)
        if workspace.is_protected(rel):
            self.refused.append({"path": rel, "why": "edit to a test file or test config"})
            raise ToolError(f"{rel} is a test file or test config, and those are read-only. "
                            "Change the code under test instead.")
        _, rel, text = self._text(path)
        if not old_string:
            raise ToolError("old_string is empty")
        if old_string == new_string:
            raise ToolError("old_string and new_string are the same")
        count = text.count(old_string)
        if count == 0 and "\r\n" in text and "\n" in old_string:   # a CRLF file: read_file shows lines without \r
            old_string, new_string = old_string.replace("\n", "\r\n"), new_string.replace("\n", "\r\n")
            count = text.count(old_string)
        if count == 0:
            raise ToolError(f"old_string isn't in {rel}. It must match exactly, whitespace included: "
                            "read the lines again and copy them.")
        if count > 1 and not replace_all:
            raise ToolError(f"old_string appears {count} times in {rel}. Include nearby lines to make it "
                            "unique, or set replace_all.")
        at = text.index(old_string)
        new_text = text.replace(old_string, new_string, -1 if replace_all else 1)
        full.write_bytes(new_text.encode("utf-8"))
        self.edits += 1
        first = text[:at].count("\n") + 1
        lines = new_text.split("\n")
        lo, hi = max(1, first - 2), min(len(lines), first + new_string.count("\n") + 2)
        n = count if replace_all else 1
        return (f"Edited {rel} ({n} replacement{'s' if n > 1 else ''}). Lines {lo}-{hi} now:\n"
                + self._numbered(lines[lo - 1:hi], lo))

    def run_tests(self) -> str:
        if len(self.test_runs) >= self.max_test_runs:
            raise ToolError(f"no test runs left (the limit is {self.max_test_runs}). Finish now: say what "
                            "you changed and what you found.")
        run = self.runner(self.root, self.command)
        self.test_runs.append(run)
        left = self.max_test_runs - len(self.test_runs)
        if run.error:
            raise ToolError(f"the sandbox failed, so the tests didn't run: {run.error}")
        if run.passed:
            head = "All tests passed."
        elif run.timed_out:
            head = f"The tests hung and were stopped after {run.timeout:g} seconds."
        else:
            head = "Tests failed."
        return f"{head} {run.summary()}. Test runs left: {left}.\n\n{run.output}"
