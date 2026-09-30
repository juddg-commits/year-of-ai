"""Copying, guarding and diffing the workspace, and the five tools. Offline and free."""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from code_fixer import workspace
from code_fixer.sandbox import TestRun
from code_fixer.tools import ToolBox, ToolError, check_args

from tests.fakes import BUGGY, calc_runner, make_repo


class Copying(unittest.TestCase):
    def test_skips_clutter_secrets_virtualenvs_and_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp) / "src", Path(tmp) / "dst"
            make_repo(src)
            for rel in [".git/config", "__pycache__/x.pyc", "node_modules/a.js", "pkg.egg-info/PKG-INFO",
                        "myenv/pyvenv.cfg", "myenv/lib/site.py", ".env", ".env.local", "certs/server.pem"]:
                (src / rel).parent.mkdir(parents=True, exist_ok=True)
                (src / rel).write_text("x")
            (src / "link.py").symlink_to(src / "calc.py")
            (src / "linkdir").symlink_to(src / "certs")
            copied = workspace.copy_repo(src, dst)
            self.assertEqual(copied.files, ["calc.py", "test_calc.py"])
            skipped = dict(copied.skipped)
            self.assertEqual(skipped[".env"], "may hold secrets")
            self.assertEqual(skipped["certs/server.pem"], "may hold secrets")
            self.assertEqual(skipped["link.py"], "symlink")
            self.assertEqual(skipped["linkdir/"], "symlink")
            self.assertFalse((dst / ".env").exists())

    def test_protected_files_are_tests_and_test_config(self):
        for rel in ["test_calc.py", "calc_test.py", "tests/helpers.py", "tests/data/case.json", "pkg/test/x.py",
                    "conftest.py", "pkg/conftest.py", "pyproject.toml", "setup.cfg", "tox.ini", "pytest.ini", "tests.py"]:
            self.assertTrue(workspace.is_protected(rel), rel)
        for rel in ["calc.py", "pkg/testing_utils.py", "src/contest.py", "latest.py", "setup.py", "attest/x.py"]:
            self.assertFalse(workspace.is_protected(rel), rel)

    def test_paths_cannot_leave_the_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_repo(Path(tmp) / "repo")
            self.assertEqual(workspace.resolve_inside(root, "./calc.py")[1], "calc.py")
            for bad in ["../outside.py", "/etc/passwd", "calc.py/../../x", "", "a\x00b"]:
                with self.assertRaises(ValueError, msg=bad):
                    workspace.resolve_inside(root, bad)


class Diffs(unittest.TestCase):
    CASES = {
        "plain.py": ("a = 1\nb = 2\n", "a = 1\nb = 3\n"),
        "no_newline.py": ("x = 1\ny = 2", "x = 1\ny = 20"),
        "crlf.py": ("one\r\ntwo\r\n", "one\r\nTWO\r\n"),
        "formfeed.py": ("a = 1\n\x0cb = 2\nc = 3\n", "a = 1\n\x0cb = 2\nc = 4\n"),
    }

    def test_the_patch_applies_to_the_original(self):
        tool = shutil.which("patch")
        if not tool:
            self.skipTest("no patch tool")
        with tempfile.TemporaryDirectory() as tmp:
            before, after, target = Path(tmp) / "before", Path(tmp) / "after", Path(tmp) / "target"
            for root in (before, after, target):
                root.mkdir()
            for name, (old, new) in self.CASES.items():
                for root, content in ((before, old), (after, new), (target, old)):
                    (root / name).write_bytes(content.encode())
            diff = workspace.make_diff(before, after, sorted(self.CASES))
            self.assertIn("\\ No newline at end of file", diff)
            r = subprocess.run([tool, "-p1", "--forward", "--silent"], input=diff.encode(), cwd=target,
                               capture_output=True)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            for name, (_, new) in self.CASES.items():
                self.assertEqual((target / name).read_bytes(), new.encode(), name)

    def test_restore_protected_puts_tests_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            pristine = make_repo(Path(tmp) / "a")
            dest = Path(tmp) / "b"
            shutil.copytree(pristine, dest)
            (dest / "test_calc.py").write_text("tampered")
            (dest / "calc.py").write_text("edited")
            changed = workspace.restore_protected(pristine, dest, ["calc.py", "test_calc.py"])
            self.assertEqual(changed, ["test_calc.py"])
            self.assertEqual((dest / "test_calc.py").read_text(), (pristine / "test_calc.py").read_text())
            self.assertEqual((dest / "calc.py").read_text(), "edited")


class Tools(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_repo(Path(self.tmp.name) / "ws")
        (self.root / "pkg").mkdir()
        (self.root / "pkg" / "long.py").write_text("".join(f"line_{n} = {n}\n" for n in range(1, 501)))
        (self.root / "crlf.py").write_bytes(b"def f():\r\n    return 1\r\n")
        files = sorted(["calc.py", "test_calc.py", "pkg/long.py", "crlf.py"])
        self.box = ToolBox(self.root, files, ["pytest"], calc_runner(), max_test_runs=2)

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_files_marks_read_only(self):
        out = self.box.call("list_files", {})
        self.assertIn("test_calc.py (", out)
        self.assertIn("[read-only]", out.split("test_calc.py")[1].splitlines()[0])
        self.assertEqual(self.box.call("list_files", {"path": "pkg"}).count("\n"), 0)

    def test_read_file_numbers_lines_and_pages_long_files(self):
        out = self.box.call("read_file", {"path": "calc.py"})
        self.assertEqual(out, "calc.py, lines 1-2 of 2:\n    1\tdef add(a, b):\n    2\t    return a - b")
        out = self.box.call("read_file", {"path": "pkg/long.py"})
        self.assertIn("lines 1-400 of 500", out)
        self.assertIn("[100 more lines: read_file with start_line=401]", out)
        out = self.box.call("read_file", {"path": "pkg/long.py", "start_line": 499, "end_line": 900})
        self.assertTrue(out.endswith("  500\tline_500 = 500"))
        with self.assertRaises(ToolError):
            self.box.call("read_file", {"path": "pkg"})

    def test_search(self):
        self.assertEqual(self.box.call("search", {"pattern": r"return a [-+] b"}), "calc.py:2: return a - b")
        self.assertEqual(self.box.call("search", {"pattern": "nothing_like_this"}), "No matches.")
        self.assertIn("stopped at 100", self.box.call("search", {"pattern": "line_", "path": "pkg"}))
        with self.assertRaises(ToolError):
            self.box.call("search", {"pattern": "add("})

    def test_edit_file(self):
        out = self.box.call("edit_file", {"path": "calc.py", "old_string": "a - b", "new_string": "a + b"})
        self.assertIn("Edited calc.py (1 replacement)", out)
        self.assertIn("return a + b", out)
        self.assertEqual((self.root / "calc.py").read_text(), "def add(a, b):\n    return a + b\n")
        with self.assertRaisesRegex(ToolError, "isn't in calc.py"):
            self.box.call("edit_file", {"path": "calc.py", "old_string": "a - b", "new_string": "x"})
        (self.root / "calc.py").write_text("x = 1\nx = 1\n")
        with self.assertRaisesRegex(ToolError, "appears 2 times"):
            self.box.call("edit_file", {"path": "calc.py", "old_string": "x = 1", "new_string": "x = 2"})
        self.box.call("edit_file", {"path": "calc.py", "old_string": "x = 1", "new_string": "x = 2", "replace_all": True})
        self.assertEqual((self.root / "calc.py").read_text(), "x = 2\nx = 2\n")

    def test_edits_keep_crlf_line_endings(self):
        self.box.call("edit_file", {"path": "crlf.py", "old_string": "def f():\n    return 1",
                                    "new_string": "def f():\n    return 2"})
        self.assertEqual((self.root / "crlf.py").read_bytes(), b"def f():\r\n    return 2\r\n")

    def test_guards(self):
        with self.assertRaisesRegex(ToolError, "read-only"):
            self.box.call("edit_file", {"path": "test_calc.py", "old_string": "== 5", "new_string": "== -1"})
        with self.assertRaisesRegex(ToolError, "outside the repo"):
            self.box.call("read_file", {"path": "../../etc/hosts"})
        self.assertEqual([r["path"] for r in self.box.refused], ["test_calc.py", "../../etc/hosts"])
        self.assertIn("5", (self.root / "test_calc.py").read_text())
        with self.assertRaisesRegex(ToolError, "unknown tool"):
            self.box.call("delete_file", {"path": "calc.py"})

    def test_run_tests_counts_down(self):
        self.assertIn("Tests failed. 1 failed, 1 passed (exit 1). Test runs left: 1.", self.box.call("run_tests", {}))
        self.box.call("edit_file", {"path": "calc.py", "old_string": "a - b", "new_string": "a + b"})
        self.assertIn("All tests passed. 2 passed (exit 0). Test runs left: 0.", self.box.call("run_tests", {}))
        with self.assertRaisesRegex(ToolError, "no test runs left"):
            self.box.call("run_tests", {})
        self.assertEqual(len(self.box.test_runs), 2)

    def test_arguments_are_checked(self):
        with self.assertRaisesRegex(ToolError, "missing path"):
            check_args("read_file", {})
        with self.assertRaisesRegex(ToolError, "unknown mode"):
            check_args("read_file", {"path": "a", "mode": "x"})
        with self.assertRaisesRegex(ToolError, "start_line must be an integer"):
            check_args("read_file", {"path": "a", "start_line": True})
        with self.assertRaisesRegex(ToolError, "JSON object"):
            check_args("run_tests", "now")


if __name__ == "__main__":
    unittest.main()
