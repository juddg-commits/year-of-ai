"""Eval cases: a source project, a bug planted in it, and a split of its tests.

A case is a JSON file in evals/cases/. Its source is a project in evals/sources/ (third-party
code keeps its LICENSE). Materializing a case copies the source, plants the bug (each edit's
`find` must occur exactly once) and leaves out the hidden test files; only the grader adds them
back. The agent never sees evals/: its tools can't leave the workspace.

A real bug's source is fetched, not committed: see realbugs.py. Hidden tests written for such a
case live in evals/hidden/<case id>/, laid over the source only when the hidden tests go back in.
They add files; they never replace one of upstream's.

A case is usable only once verify() passes: the source without the bug passes every test,
visible and hidden, and the bug fails at least one visible test."""

import json
import re
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from . import config, realbugs, sandbox, workspace

CASES_DIR = config.EVALS_DIR / "cases"
SOURCES_DIR = config.EVALS_DIR / "sources"
HIDDEN_DIR = config.EVALS_DIR / "hidden"   # hidden tests we wrote for cases whose source is fetched


@dataclass
class Case:
    id: str
    source: str            # a directory in evals/sources/
    split: str             # "dev" (tune on it) or "test" (held out: report only this)
    kind: str              # "planted" (a bug put into third-party code), "handwritten", or "real" (a merged fix, undone)
    category: str          # off-by-one, flipped comparison, wrong variable, missed edge case, wrong assumption
    note: str              # what the bug is: for the grader and the write-up, never shown to the agent
    bug: list              # [{"file", "find", "replace"}]
    hidden: list = field(default_factory=list)   # test files (relative to the source) the agent never sees
    test_command: str = config.DEFAULT_TEST_COMMAND

    @property
    def command(self) -> list:
        return shlex.split(self.test_command)

    def is_hidden(self, test_id: str) -> bool:
        path = test_id.split("::")[0]
        return any(path == h or path.startswith(h.rstrip("/") + "/") for h in self.hidden)


def load(ids: list | None = None, split: str | None = None, cases_dir: Path = CASES_DIR) -> list:
    found = []
    for path in sorted(Path(cases_dir).glob("*.json")):
        case = Case(**json.loads(path.read_text()))
        if case.id != path.stem:
            raise ValueError(f"{path.name}: id {case.id!r} doesn't match the file name")
        if (ids is None or case.id in ids) and (split is None or case.split == split):
            found.append(case)
    if ids:
        missing = set(ids) - {c.id for c in found}
        if missing:
            raise ValueError(f"no such case: {', '.join(sorted(missing))}")
    return found


def ours(case: Case, hidden_dir: Path = HIDDEN_DIR) -> list:
    """The hidden test files written for this case (paths relative to the source), if any."""
    root = Path(hidden_dir) / case.id
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts) if root.is_dir() else []


def materialize(case: Case, dest: Path, *, bug: bool = True, hidden: bool = False,
                sources_dir: Path = SOURCES_DIR, hidden_dir: Path = HIDDEN_DIR) -> Path:
    dest, origin = Path(dest), Path(sources_dir) / case.source
    if not origin.exists():
        raise FileNotFoundError(f"{case.id}: source {case.source} isn't here yet: python eval.py fetch gets it")
    shutil.copytree(origin, dest,
                    ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    added = ours(case, hidden_dir)
    for rel in added:
        if not case.is_hidden(rel):
            raise ValueError(f"{case.id}: {rel} is in {Path(hidden_dir).name}/{case.id}/ but not in the case's hidden list")
        if (dest / rel).exists():
            raise ValueError(f"{case.id}: {rel} would replace upstream's file")
        if hidden:
            (dest / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path(hidden_dir) / case.id / rel, dest / rel)
    if not hidden:
        for rel in case.hidden:
            target = dest / rel
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
            elif rel not in added:
                raise ValueError(f"{case.id}: hidden path {rel} doesn't exist")
    if bug:
        for edit in case.bug:
            path = dest / edit["file"]
            text = path.read_bytes().decode("utf-8")
            n = text.count(edit["find"])
            if n != 1:
                raise ValueError(f"{case.id}: the bug's find text occurs {n} times in {edit['file']}, not once")
            path.write_bytes(text.replace(edit["find"], edit["replace"]).encode("utf-8"))
    return dest


def ensure_sources(chosen: list, sources_dir: Path = SOURCES_DIR, log: Callable = print) -> None:
    """Fetch every real-bug source these cases need that isn't on this machine yet. Network, so it
    runs before any paid call."""
    specs = realbugs.manifest()
    for name in sorted({c.source for c in chosen if realbugs.is_fetched(c.source)}):
        if (Path(sources_dir) / name).exists():
            continue
        if name not in specs:
            raise ValueError(f"source {name} isn't in {realbugs.MANIFEST.name}")
        log(f"Fetching {name} from github.com/{specs[name]['repo']}…")
        realbugs.fetch(name, specs[name], sources_dir)


def verify(case: Case, runner: Callable = sandbox.run_tests, sources_dir: Path = SOURCES_DIR,
           hidden_dir: Path = HIDDEN_DIR) -> dict:
    """Free (no API calls): four sandbox runs. Returns the verdict with the evidence."""
    problems = []
    for edit in case.bug:
        if workspace.is_protected(edit["file"]):
            problems.append(f"the bug is in {edit['file']}, which the agent can't edit")
    for rel in case.hidden:
        if not workspace.is_protected(rel):
            problems.append(f"hidden path {rel} isn't a test file")
    with tempfile.TemporaryDirectory(prefix="code-fixer-case-") as tmp:
        tmp = Path(tmp)
        dirs = dict(sources_dir=sources_dir, hidden_dir=hidden_dir)
        reference = runner(materialize(case, tmp / "reference", bug=False, hidden=True, **dirs), case.command)
        clean = runner(materialize(case, tmp / "clean", bug=False, **dirs), case.command)
        visible = runner(materialize(case, tmp / "visible", **dirs), case.command)
        full = runner(materialize(case, tmp / "full", hidden=True, **dirs), case.command)
    if not reference.passed:
        problems.append(f"without the bug, the tests don't all pass: {reference.summary()}")
    elif not clean.passed:   # the visible tests must fail because of the bug, not because a file was hidden
        problems.append(f"without the bug and the hidden files, the visible tests don't all pass: {clean.summary()}")
    if visible.passed:
        problems.append("the bug doesn't fail any visible test")
    failing = [t for t, o in full.outcomes.items() if o in ("failed", "error")]
    hidden_failing = [t for t in failing if case.is_hidden(t)]
    return {"case": case.id, "ok": not problems, "problems": problems,
            "reference": reference.summary(), "visible": visible.summary(),
            "visible_failing": [t for t, o in visible.outcomes.items() if o in ("failed", "error")],
            "hidden_failing": hidden_failing, "hidden_catches": bool(hidden_failing)}


def grade(case: Case, changed: dict, runner: Callable = sandbox.run_tests,
          sources_dir: Path = SOURCES_DIR, hidden_dir: Path = HIDDEN_DIR) -> sandbox.TestRun:
    """The broken case with the hidden tests back and the agent's changed files on top."""
    with tempfile.TemporaryDirectory(prefix="code-fixer-grade-") as tmp:
        dest = materialize(case, Path(tmp) / "grade", hidden=True, sources_dir=sources_dir, hidden_dir=hidden_dir)
        for rel, text in changed.items():
            if not workspace.is_protected(rel):   # the tools refuse such edits; belt and braces
                (dest / rel).write_bytes(text.encode("utf-8"))
        return runner(dest, case.command)


def patched_files(case: Case, diff: str, sources_dir: Path = SOURCES_DIR, hidden_dir: Path = HIDDEN_DIR) -> dict:
    """A saved patch as grade() takes it: the broken case with the patch applied, the files it
    changes. For re-grading old runs against new hidden tests, free."""
    with tempfile.TemporaryDirectory(prefix="code-fixer-regrade-") as tmp:
        dest = materialize(case, Path(tmp) / "case", sources_dir=sources_dir, hidden_dir=hidden_dir)
        # -F 0: no fuzz. A hunk that only fits somewhere nearby would grade a different patch.
        applied = subprocess.run(["patch", "-p1", "-s", "-f", "-F", "0"], input=diff, cwd=dest, capture_output=True, text=True)
        if applied.returncode:
            raise ValueError(f"{case.id}: the patch doesn't apply: {(applied.stdout + applied.stderr).strip()[:300]}")
        changed = re.findall(r"^\+\+\+ b/(\S+)", diff, re.M)
        return {rel: (dest / rel).read_bytes().decode("utf-8") for rel in changed if (dest / rel).is_file()}


def as_dict(case: Case) -> dict:
    return asdict(case)
