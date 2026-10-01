"""Real bugs (offline): a fix reversed into edits, a fetched source unpacked safely, and sources
fetched only when missing. No network: the tarball is built here and fetch() is faked."""

import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from code_fixer import cases, realbugs

FIXED = "".join(f"line {i}\n" for i in range(1, 41))


def replay(text: str, edits: list) -> str:
    """The way cases.materialize plants a bug: each find exactly once, in order."""
    for edit in edits:
        assert text.count(edit["find"]) == 1, edit["find"]
        text = text.replace(edit["find"], edit["replace"])
    return text


class ReverseEdits(unittest.TestCase):
    def test_two_far_apart_changes_become_two_edits(self):
        buggy = FIXED.replace("line 5\n", "line five\n").replace("line 30\n", "")
        edits = realbugs.reverse_edits("m.py", FIXED, buggy)
        self.assertEqual(len(edits), 2)
        self.assertEqual(replay(FIXED, edits), buggy)

    def test_lines_the_fix_added_are_taken_out(self):
        fixed = FIXED.replace("line 20\n", "line 20\n    guard()\n    guard()\n")
        edits = realbugs.reverse_edits("m.py", fixed, FIXED)
        self.assertEqual(replay(fixed, edits), FIXED)

    def test_repeated_context_grows_until_unique(self):
        block = "    x = 1\n    y = 2\n    z = 3\n    return x\n"
        fixed = "def a():\n" + block + "\n\ndef b():\n" + block
        buggy = fixed[:fixed.rindex("return x")] + "return y\n"   # only b() changes
        edits = realbugs.reverse_edits("m.py", fixed, buggy)
        self.assertEqual(replay(fixed, edits), buggy)
        self.assertIn("def b():", edits[0]["find"])   # b()'s body is a()'s: only its def line is unique

    def test_no_change_no_edits(self):
        self.assertEqual(realbugs.reverse_edits("m.py", FIXED, FIXED), [])


def tarball(members: dict, links: dict = None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, text in members.items():
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        for name, target in (links or {}).items():
            info = tarfile.TarInfo(name)
            info.type, info.linkname = tarfile.SYMTYPE, target
            tar.addfile(info)
    return buf.getvalue()


class Unpack(unittest.TestCase):
    def test_only_the_kept_paths_and_regular_files(self):
        data = tarball({"proj-abc/pkg/core.py": "x = 1\n", "proj-abc/tests/test_core.py": "def test(): pass\n",
                        "proj-abc/tests/needs_submodule.py": "", "proj-abc/CHANGES.md": "Fixed the bug in core.\n",
                        "proj-abc/LICENSE": "MIT\n", "proj-abc/../evil.py": "boom\n"},
                       links={"proj-abc/pkg/link.py": "/etc/passwd"})
        with tempfile.TemporaryDirectory() as tmp:
            dest = realbugs.unpack(data, Path(tmp) / "proj@abc", {
                "keep": ["pkg", "tests", "LICENSE"], "drop": ["tests/needs_submodule.py"],
                "files": {"pyproject.toml": "[tool.pytest.ini_options]\n"}})
            found = sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*") if p.is_file())
            self.assertEqual(found, ["LICENSE", "pkg/core.py", "pyproject.toml", "tests/test_core.py"])
            self.assertFalse((Path(tmp) / "evil.py").exists())
            self.assertFalse((Path(tmp) / "proj@abc.partial").exists())


class Download(unittest.TestCase):
    def test_a_cut_off_download_is_a_clean_error(self):
        data = tarball({"proj-abc/pkg/core.py": "x = 1\n" * 5000})
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = data[:len(data) // 2]
        with tempfile.TemporaryDirectory() as tmp, mock.patch("urllib.request.urlopen", return_value=response):
            with self.assertRaisesRegex(ValueError, "isn't a readable tarball"):
                realbugs.fetch("proj@abc", {"repo": "o/proj", "commit": "abc", "keep": ["pkg"]}, Path(tmp))


class Fetching(unittest.TestCase):
    def case(self, source: str) -> cases.Case:
        return cases.Case(id=source.replace("@", "-"), source=source, split="dev", kind="real",
                          category="x", note="", bug=[])

    def test_only_missing_sources_are_fetched(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "have@1").mkdir()
            specs = {"have@1": {"repo": "o/have"}, "need@2": {"repo": "o/need"}}
            with mock.patch.object(realbugs, "manifest", return_value=specs), \
                    mock.patch.object(realbugs, "fetch") as fetch:
                cases.ensure_sources([self.case("have@1"), self.case("need@2"), self.case("need@2")],
                                     sources_dir=Path(tmp), log=lambda msg: None)
            fetch.assert_called_once_with("need@2", specs["need@2"], Path(tmp))

    def test_a_source_missing_from_the_manifest_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(realbugs, "manifest", return_value={}):
            with self.assertRaisesRegex(ValueError, "isn't in"):
                cases.ensure_sources([self.case("lost@3")], sources_dir=Path(tmp), log=lambda msg: None)

    def test_materializing_an_unfetched_source_says_how_to_get_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(FileNotFoundError, "eval.py fetch"):
                cases.materialize(self.case("gone@4"), Path(tmp) / "out", sources_dir=Path(tmp))


if __name__ == "__main__":
    unittest.main()
