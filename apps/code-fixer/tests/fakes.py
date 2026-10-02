"""Stand-ins for the API and the sandbox, so the loop can be tested for free."""

import itertools
from pathlib import Path
from types import SimpleNamespace as NS

from code_fixer.sandbox import TestRun

BUGGY = "def add(a, b):\n    return a - b\n"
FIXED = "def add(a, b):\n    return a + b\n"
TESTS = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n\n\ndef test_zero():\n    assert add(0, 0) == 0\n"


def make_repo(root: Path, calc: str = BUGGY) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "calc.py").write_text(calc)
    (root / "test_calc.py").write_text(TESTS)
    return root


def calc_runner(calls: list | None = None):
    """Passes when calc.py adds. Records each workspace it ran on."""
    def run(workspace, command):
        if calls is not None:
            calls.append(Path(workspace))
        ok = "a + b" in (Path(workspace) / "calc.py").read_text()
        outcomes = {"test_calc.py::test_add": "passed" if ok else "failed", "test_calc.py::test_zero": "passed"}
        output = ("2 passed" if ok else "FAILED test_calc.py::test_add - assert -1 == 5\n1 failed, 1 passed")
        return TestRun(list(command), exit_code=0 if ok else 1, outcomes=outcomes, output=output, output_chars=len(output))
    return run


def usage(inp=100, out=200, read=0, write=0, iterations=None):
    return NS(input_tokens=inp, output_tokens=out, cache_read_input_tokens=read,
              cache_creation_input_tokens=write, iterations=iterations)


_ids = itertools.count(1)


def tool(name, args):
    return NS(type="tool_use", id=f"toolu_{next(_ids):04d}", name=name, input=args)


def text(t):
    return NS(type="text", text=t)


def thinking(t=""):
    return NS(type="thinking", thinking=t, signature="sig")


def response(*blocks, stop=None, u=None, model="claude-opus-5-5"):
    if stop is None:
        stop = "tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn"
    return NS(content=list(blocks), stop_reason=stop, model=model, usage=u or usage())


class _Stream:
    def __init__(self, item):
        self.item = item

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.item


class FakeMessages:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []    # the kwargs of every call, with a snapshot of the messages list

    def stream(self, **kwargs):
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        if not self.script:
            raise AssertionError("the model was called more times than the test scripted")
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return _Stream(item)


class FakeClient:
    def __init__(self, script):
        self.beta = NS(messages=FakeMessages(script))
        self.options = []     # the kwargs of every with_options call (one per model call)

    def with_options(self, **kwargs):
        self.options.append(kwargs)
        return self

    @property
    def requests(self):
        return self.beta.messages.requests


class APIStatusError(Exception):
    """Shaped like the SDK's: carries status_code."""

    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code
