"""Run a repo's tests in a locked-down container: the only place the repo's code ever runs.

Each run gets a fresh container with no network, 512 MB of memory, one CPU, 256 processes,
no Linux capabilities and a read-only root filesystem. The workspace is mounted read-only
and the tests run on a scratch copy inside the container (sandbox/run-tests.sh), so nothing
they write survives. A run that outlives its timeout is killed, and so is one whose caller
is interrupted: stopping the docker client alone would leave the container running, so the
kill is in `finally`."""

import re
import shutil
import subprocess
import threading
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import config

# pytest's -rA summary: "PASSED tests/test_a.py::test_x", "FAILED tests/test_a.py::test_y - AssertionError…"
SUMMARY_HEADER = re.compile(r"^=+ short test summary info =+$")
SUMMARY_LINE = re.compile(r"^(PASSED|FAILED|ERROR|XFAIL|XPASS) (.+?)(?: - .*)?$")
OUTCOME = {"PASSED": "passed", "FAILED": "failed", "ERROR": "error", "XFAIL": "xfailed", "XPASS": "xpassed"}


@dataclass
class TestRun:
    command: list
    exit_code: int | None = None
    seconds: float = 0.0
    timed_out: bool = False
    output: str = ""                 # head + tail of stdout/stderr, see trim()
    output_chars: int = 0            # before trimming
    outcomes: dict = field(default_factory=dict)   # pytest node id -> passed/failed/error/xfailed/xpassed
    error: str | None = None         # the sandbox itself failed (no docker, no image); the tests never ran
    timeout: float = config.TEST_TIMEOUT

    @property
    def passed(self) -> bool:
        return self.error is None and not self.timed_out and self.exit_code == 0

    def summary(self) -> str:
        if self.error:
            return f"sandbox error: {self.error}"
        if self.timed_out:
            return f"stopped after {self.timeout:g}s (hung)"
        counts = Counter(self.outcomes.values())
        if not counts:
            return f"exit {self.exit_code}"
        return ", ".join(f"{n} {name}" for name, n in sorted(counts.items())) + f" (exit {self.exit_code})"

    def as_dict(self) -> dict:
        return {"command": self.command, "exit_code": self.exit_code, "seconds": round(self.seconds, 2),
                "timed_out": self.timed_out, "error": self.error, "summary": self.summary(),
                "outcomes": self.outcomes, "output_chars": self.output_chars, "output": self.output}


def docker_bin() -> str | None:
    """OrbStack puts docker in ~/.orbstack/bin, which a process started outside a login shell may not have on PATH."""
    found = shutil.which("docker")
    if found:
        return found
    orbstack = Path.home() / ".orbstack" / "bin" / "docker"
    return str(orbstack) if orbstack.exists() else None


def is_pytest(command: list) -> bool:
    return any(Path(arg).name in ("pytest", "py.test") for arg in command)


def with_summary_flag(command: list) -> list:
    """Ask pytest for a one-line result per test (-rA), so outcomes can be compared test by test."""
    return list(command) + ["-rA"] if is_pytest(command) and "-rA" not in command else list(command)


def parse_outcomes(output: str) -> dict:
    outcomes, in_summary = {}, False
    for line in output.splitlines():
        if SUMMARY_HEADER.match(line.strip()):
            in_summary = True
            continue
        if in_summary:
            m = SUMMARY_LINE.match(line.rstrip())
            if m:
                outcomes[m[2]] = OUTCOME[m[1]]
    return outcomes


def trim(text: str, head: int = config.TEST_OUTPUT_HEAD, tail: int = config.TEST_OUTPUT_TAIL) -> str:
    if len(text) <= head + tail:
        return text
    cut = len(text) - head - tail
    return f"{text[:head]}\n\n[… {cut:,} characters cut …]\n\n{text[-tail:]}"


class _Capture:
    """Reads a process's output in the background, keeping the start and the end.
    A test that prints in a loop can't fill memory: everything past the ends is dropped."""

    def __init__(self, stream, keep_head: int = 200_000, keep_tail: int = 400_000):
        self.head, self.tail, self.total = bytearray(), bytearray(), 0
        self.keep_head, self.keep_tail = keep_head, keep_tail
        self.thread = threading.Thread(target=self._read, args=(stream,), daemon=True)
        self.thread.start()

    def _read(self, stream) -> None:
        try:
            for chunk in iter(lambda: stream.read(65536), b""):
                self.total += len(chunk)
                room = self.keep_head - len(self.head)
                if room > 0:
                    self.head += chunk[:room]
                    chunk = chunk[room:]
                if chunk:
                    self.tail += chunk
                    del self.tail[:-self.keep_tail]
        except (OSError, ValueError):   # the pipe was closed under us (an interrupted run)
            pass

    def text(self) -> str:
        self.thread.join(timeout=5)
        dropped = self.total - len(self.head) - len(self.tail)
        middle = f"\n[… {dropped:,} bytes of output dropped …]\n" if dropped > 0 else ""
        return self.head.decode(errors="replace") + middle + self.tail.decode(errors="replace")


def run_tests(workspace: Path, command: list, timeout: float = config.TEST_TIMEOUT) -> TestRun:
    """Run `command` (an argv list) against a copy of `workspace` in a fresh container."""
    command = with_summary_flag(command)
    docker = docker_bin()
    if not docker:
        return TestRun(command, error="docker not found: install and open OrbStack")
    name = f"code-fixer-{uuid.uuid4().hex[:12]}"
    args = [docker, "run", "--rm", "--name", name, "--pull", "never",
            "--network", "none", "--memory", "512m", "--memory-swap", "512m", "--cpus", "1",
            "--pids-limit", "256", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--read-only", "--tmpfs", "/work:rw,exec,size=256m,mode=1777",
            "--tmpfs", "/tmp:rw,exec,size=64m,mode=1777",
            "-v", f"{Path(workspace).resolve()}:/src:ro", config.IMAGE, *command]
    start = time.monotonic()
    run = TestRun(command, timeout=timeout)
    proc = None
    gone = False    # True once the container has exited or been removed
    try:
        proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        capture = _Capture(proc.stdout)
        try:
            run.exit_code = proc.wait(timeout=timeout)
            gone = True
        except subprocess.TimeoutExpired:
            run.timed_out = True
            _kill(docker, name, proc)
            gone = True
        run.seconds = time.monotonic() - start
        text = capture.text()
    except OSError as e:
        return TestRun(command, error=f"couldn't start docker: {e}")
    finally:
        if proc is not None and not gone:   # interrupted (Ctrl-C, a cancelled job): the container must still stop
            _kill(docker, name, proc)
        if proc is not None and proc.stdout:
            proc.stdout.close()
    run.output_chars = len(text)
    run.output = trim(text)
    run.outcomes = parse_outcomes(text)
    if run.exit_code == 125:   # docker itself failed (no image, daemon down); the tests never ran
        run.error = text.strip()[-500:] or "docker run failed (exit 125)"
        if "Unable to find image" in text or "No such image" in text:
            run.error = f"sandbox image {config.IMAGE} not built: docker build -t {config.IMAGE} sandbox"
    return run


def _kill(docker: str, name: str, proc: subprocess.Popen) -> None:
    subprocess.run([docker, "rm", "--force", name], capture_output=True, timeout=30)
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=10)


def image_id() -> str | None:
    docker = docker_bin()
    if not docker:
        return None
    r = subprocess.run([docker, "image", "inspect", "--format", "{{.Id}}", config.IMAGE],
                       capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        return None
    return r.stdout.strip() or None
