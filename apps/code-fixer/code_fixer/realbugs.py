"""Real bugs: a merged fix from an open-source project, undone.

The source is the project at the fix's merge commit, fetched from GitHub into evals/sources/
under name@commit (git-ignored: the public repo keeps only the case and where to fetch it).
Only the paths a case needs are kept (the package, its tests, the LICENSE): never a changelog
that describes the fix. The bug is the fix reversed: edits that turn each non-test file the fix
changed back into its text before the fix. The fix's own tests stay, so they fail: they are the
agent's bug report."""

import difflib
import io
import json
import shutil
import tarfile
import urllib.request
from pathlib import Path

from . import config

MANIFEST = config.EVALS_DIR / "sources.json"   # fetched sources: name -> {repo, commit, keep, drop, files}
CONTEXT = 2          # lines of context around each edit, grown until every edit applies exactly once
MAX_CONTEXT = 12


def is_fetched(source: str) -> bool:
    return "@" in source


def manifest() -> dict:
    return json.loads(MANIFEST.read_text()) if MANIFEST.exists() else {}


def _under(rel: Path, paths: list) -> bool:
    return any(rel == Path(p) or Path(p) in rel.parents for p in paths)


def unpack(data: bytes, dest: Path, spec: dict) -> Path:
    """A GitHub tarball (one top-level folder) into `dest`: only the `keep` paths, minus `drop`,
    regular files and folders only, then the `files` written over it."""
    partial = dest.with_name(dest.name + ".partial")
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            parts = Path(member.name).parts[1:]
            if not parts or ".." in parts or Path(member.name).is_absolute():
                continue
            rel = Path(*parts)
            if not _under(rel, spec["keep"]) or _under(rel, spec.get("drop", [])):
                continue
            if member.isdir():
                (partial / rel).mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                (partial / rel).parent.mkdir(parents=True, exist_ok=True)
                (partial / rel).write_bytes(tar.extractfile(member).read())
    for rel, text in spec.get("files", {}).items():
        (partial / rel).parent.mkdir(parents=True, exist_ok=True)
        (partial / rel).write_text(text)
    shutil.rmtree(dest, ignore_errors=True)
    partial.rename(dest)
    return dest


def fetch(name: str, spec: dict, sources_dir: Path) -> Path:
    url = f"https://codeload.github.com/{spec['repo']}/tar.gz/{spec['commit']}"
    with urllib.request.urlopen(url, timeout=120) as response:
        data = response.read()
    try:
        return unpack(data, Path(sources_dir) / name, spec)
    except (tarfile.TarError, EOFError) as e:   # neither is an OSError: a cut-off download would escape as a traceback
        raise ValueError(f"{name}: the download from {url} isn't a readable tarball ({e})") from e


def _apply_once(text: str, find: str, replace: str):
    at = text.find(find)
    if not find or at < 0 or text.find(find, at + 1) >= 0:
        return None
    return text[:at] + replace + text[at + len(find):]


def reverse_edits(file: str, fixed: str, buggy: str) -> list:
    """Edits that turn `fixed` back into `buggy` when applied in order, each `find` occurring
    exactly once in the text it's applied to. More context merges nearby hunks and makes repeated
    lines unique."""
    a, b = fixed.splitlines(keepends=True), buggy.splitlines(keepends=True)
    for n in range(CONTEXT, MAX_CONTEXT + 1):
        groups = difflib.SequenceMatcher(None, a, b, autojunk=False).get_grouped_opcodes(n)
        edits = [{"file": file, "find": "".join(a[g[0][1]:g[-1][2]]), "replace": "".join(b[g[0][3]:g[-1][4]])}
                 for g in groups]
        text = fixed
        for edit in edits:
            text = _apply_once(text, edit["find"], edit["replace"]) if text is not None else None
        if text == buggy:
            return edits
    raise ValueError(f"{file}: no set of edits applies cleanly within {MAX_CONTEXT} lines of context")
