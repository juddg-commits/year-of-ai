"""The agent's copy of a repo: what gets copied, which files it may not edit, and the diff.

The original repo is never modified. fix() copies it twice: a pristine copy (the "before"
side of the diff, and where test files are restored from for the final check) and the
workspace the agent edits. Files are read and written as bytes, so line endings survive."""

import difflib
import fnmatch
import hashlib
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import config

SKIP_DIRS = {".git", ".hg", ".svn", ".tox", ".nox", "__pycache__", ".pytest_cache", ".mypy_cache",
             ".ruff_cache", "node_modules", ".idea", ".vscode", ".eggs"}
# Never copied, so the model never reads them and they never reach the API
SECRET_FILES = [".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", ".netrc", ".pypirc", "id_rsa*", "id_ed25519*"]

# Tests and pytest's config: the model can read them but never edit them
TEST_DIRS = {"tests", "test", "testing"}
TEST_CONFIG = {"conftest.py", "pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml", "noxfile.py"}


def is_protected(rel: str) -> bool:
    parts = Path(rel).parts
    name = parts[-1]
    return (name in TEST_CONFIG
            or (name.startswith("test_") and name.endswith(".py"))
            or name.endswith("_test.py") or name in ("tests.py", "test.py")
            or any(p in TEST_DIRS for p in parts[:-1]))


class RepoTooBig(ValueError):
    pass


@dataclass
class Copied:
    files: list = field(default_factory=list)      # relative POSIX paths, sorted
    skipped: list = field(default_factory=list)    # [relative path, why]
    bytes: int = 0


def copy_repo(src: Path, dst: Path) -> Copied:
    src = Path(src).resolve()
    out = Copied()
    for dirpath, dirnames, filenames in os.walk(src):   # os.walk doesn't follow symlinked directories
        here = Path(dirpath)
        rel_dir = here.relative_to(src)
        keep = []
        for d in sorted(dirnames):
            full = here / d
            if full.is_symlink():
                out.skipped.append([(rel_dir / d).as_posix() + "/", "symlink"])
            elif not (d in SKIP_DIRS or d.endswith(".egg-info") or (full / "pyvenv.cfg").exists()):
                keep.append(d)                     # (tool clutter and virtualenvs are skipped silently)
        dirnames[:] = keep
        for f in sorted(filenames):
            full = here / f
            rel = (rel_dir / f).as_posix()
            if full.is_symlink():
                out.skipped.append([rel, "symlink"])
            elif any(fnmatch.fnmatch(f, pattern) for pattern in SECRET_FILES):
                out.skipped.append([rel, "may hold secrets"])
            elif not full.is_file():
                out.skipped.append([rel, "not a regular file"])
            elif full.stat().st_size > config.MAX_FILE_BYTES:
                out.skipped.append([rel, f"{full.stat().st_size:,} bytes"])
            else:
                out.bytes += full.stat().st_size
                out.files.append(rel)
                if len(out.files) > config.MAX_REPO_FILES or out.bytes > config.MAX_REPO_BYTES:
                    raise RepoTooBig(f"the repo is over {config.MAX_REPO_FILES:,} files or "
                                     f"{config.MAX_REPO_BYTES / 1e6:.0f} MB; point it at a smaller project")
                target = Path(dst) / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(full, target)
    out.files.sort()
    return out


def resolve_inside(root: Path, path: str) -> tuple:
    """(absolute path, normalized relative path) for a model-supplied path, or ValueError if it
    leaves the repo. The workspace holds no symlinks (copy_repo skips them), so resolve() is exact."""
    if not isinstance(path, str) or not path.strip() or "\x00" in path:
        raise ValueError("give a path relative to the repo root")
    if Path(path).is_absolute():
        raise ValueError(f"{path} is absolute: give a path relative to the repo root")
    root = Path(root).resolve()
    full = (root / path).resolve()
    if full != root and not full.is_relative_to(root):
        raise ValueError(f"{path} is outside the repo")
    rel = full.relative_to(root).as_posix()
    return full, rel


def restore_protected(pristine: Path, dest: Path, files: list) -> list:
    """Copy every test file and test config back from the pristine copy. Returns the ones that
    had changed (the tools refuse such edits, so this should always be empty)."""
    changed = []
    for rel in files:
        if is_protected(rel):
            original = (Path(pristine) / rel).read_bytes()
            target = Path(dest) / rel
            if not target.exists() or target.read_bytes() != original:
                changed.append(rel)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(original)
    return changed


def digest(root: Path, files: list) -> dict:
    return {rel: hashlib.sha256((Path(root) / rel).read_bytes()).hexdigest() for rel in files}


def changed_files(before: Path, after: Path, files: list) -> list:
    """The agent can only edit existing files (it can't create or delete), so the copied list covers every change."""
    return [rel for rel in files if (Path(after) / rel).read_bytes() != (Path(before) / rel).read_bytes()]


def _lines(text: str) -> list:
    """Split on \\n only. str.splitlines() also splits on form feeds and other separators that
    Python source can contain mid-line, which would make the diff disagree with the file."""
    parts = text.split("\n")
    lines = [p + "\n" for p in parts[:-1]]
    if parts[-1]:
        lines.append(parts[-1])
    return lines


def unified_diff(rel: str, before: str, after: str) -> str:
    out = []
    for line in difflib.unified_diff(_lines(before), _lines(after), f"a/{rel}", f"b/{rel}"):
        out.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(out)


def make_diff(before: Path, after: Path, files: list) -> str:
    """A patch that `git apply` or `patch -p1` applies to the original repo."""
    parts = []
    for rel in changed_files(before, after, files):
        a, b = (Path(before) / rel).read_bytes(), (Path(after) / rel).read_bytes()
        try:
            parts.append(unified_diff(rel, a.decode("utf-8"), b.decode("utf-8")))
        except UnicodeDecodeError:
            parts.append(f"Binary files a/{rel} and b/{rel} differ\n")
    return "".join(parts)
