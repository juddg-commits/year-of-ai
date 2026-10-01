"""The sandbox: output parsing (offline), then real container runs when OrbStack and the image
are available (free, no API calls; skipped otherwise)."""

import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from code_fixer import config, sandbox
from code_fixer.sandbox import TestRun

from tests.fakes import FIXED, make_repo

PYTEST_OUTPUT = """\
.F.xE
=================================== FAILURES ===================================
PASSED tests/test_fake.py::looks_like_a_summary_but_is_in_a_traceback
=========================== short test summary info ============================
PASSED tests/test_a.py::test_one
PASSED tests/test_a.py::test_param[a-b]
FAILED tests/test_a.py::test_two - AssertionError: assert 1 == 2
ERROR tests/test_b.py - ImportError: cannot import name 'x'
XFAIL tests/test_a.py::test_three - known bug
SKIPPED [1] tests/test_a.py:30: needs network
1 failed, 2 passed, 1 xfailed, 1 error in 0.10s
"""


class Parsing(unittest.TestCase):
    def test_outcomes_come_from_the_summary_section_only(self):
        self.assertEqual(sandbox.parse_outcomes(PYTEST_OUTPUT), {
            "tests/test_a.py::test_one": "passed",
            "tests/test_a.py::test_param[a-b]": "passed",
            "tests/test_a.py::test_two": "failed",
            "tests/test_b.py": "error",
            "tests/test_a.py::test_three": "xfailed",
        })

    def test_the_summary_flag_is_added_for_pytest_only(self):
        self.assertEqual(sandbox.with_summary_flag(["python", "-m", "pytest", "-q"]), ["python", "-m", "pytest", "-q", "-rA"])
        self.assertEqual(sandbox.with_summary_flag(["pytest", "-rA"]), ["pytest", "-rA"])
        self.assertEqual(sandbox.with_summary_flag(["python", "-m", "unittest"]), ["python", "-m", "unittest"])

    def test_the_model_reads_a_count_of_passing_tests_not_their_names(self):
        out = sandbox.hide_passed(PYTEST_OUTPUT)
        self.assertNotIn("PASSED tests/test_a.py::test_one", out)
        self.assertIn("=\n[2 PASSED lines not shown]\nFAILED tests/test_a.py::test_two", out)
        self.assertIn("PASSED tests/test_fake.py::looks_like_a_summary_but_is_in_a_traceback", out)   # not the summary
        self.assertIn("SKIPPED [1] tests/test_a.py:30: needs network", out)
        self.assertEqual(sandbox.hide_passed("no summary\nPASSED x\n"), "no summary\nPASSED x\n")

    def test_trim_keeps_both_ends(self):
        text = "HEAD" + "x" * 50_000 + "TAIL"
        out = sandbox.trim(text, head=10, tail=10)
        self.assertTrue(out.startswith("HEADxxxxxx"))
        self.assertTrue(out.endswith("xxxxxxTAIL"))
        self.assertIn("49,988 characters cut", out)
        self.assertEqual(sandbox.trim("short", 10, 10), "short")

    def test_a_docker_that_hangs_is_reported_not_raised(self):
        from unittest import mock
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired(["docker"], 30)):
            self.assertIsNone(sandbox.image_id())
            proc = mock.Mock()
            proc.poll.return_value = 0
            sandbox._kill("docker", "code-fixer-x", proc)   # must not raise from a `finally`

    def test_summaries(self):
        self.assertEqual(TestRun([], exit_code=1, outcomes={"a": "failed", "b": "passed"}).summary(), "1 failed, 1 passed (exit 1)")
        self.assertEqual(TestRun([], exit_code=4).summary(), "exit 4")
        self.assertEqual(TestRun([], timed_out=True, timeout=5).summary(), "stopped after 5s (hung)")
        self.assertFalse(TestRun([], exit_code=0, error="no docker").passed)


@unittest.skipUnless(sandbox.docker_bin() and sandbox.image_id(), "needs OrbStack running and the sandbox image built")
class Live(unittest.TestCase):
    CMD = ["python", "-m", "pytest", "-q"]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_failing_then_passing(self):
        repo = make_repo(self.root / "repo")
        run = sandbox.run_tests(repo, self.CMD)
        self.assertFalse(run.passed)
        self.assertEqual(run.outcomes, {"test_calc.py::test_add": "failed", "test_calc.py::test_zero": "passed"})
        (repo / "calc.py").write_text(FIXED)
        self.assertTrue(sandbox.run_tests(repo, self.CMD).passed)

    def test_no_network_and_a_read_only_workspace(self):
        repo = self.root / "repo"
        repo.mkdir()
        (repo / "test_escape.py").write_text(
            "import socket\n\n"
            "def test_network():\n    socket.create_connection(('1.1.1.1', 80), timeout=3)\n\n"
            "def test_write_workspace():\n    open('/src/pwned.txt', 'w').write('x')\n\n"
            "def test_scratch_copy_is_writable():\n    open('scratch.txt', 'w').write('x')\n")
        run = sandbox.run_tests(repo, self.CMD)
        self.assertEqual(run.outcomes["test_escape.py::test_network"], "failed")
        self.assertEqual(run.outcomes["test_escape.py::test_write_workspace"], "failed")
        self.assertEqual(run.outcomes["test_escape.py::test_scratch_copy_is_writable"], "passed")
        self.assertEqual(sorted(p.name for p in repo.iterdir()), ["test_escape.py"])   # nothing came back

    def test_a_hung_run_is_killed_and_its_container_removed(self):
        repo = self.root / "repo"
        repo.mkdir()
        (repo / "test_hang.py").write_text("import time\n\ndef test_hang():\n    time.sleep(60)\n")
        start = time.monotonic()
        run = sandbox.run_tests(repo, self.CMD, timeout=3)
        self.assertTrue(run.timed_out)
        self.assertLess(time.monotonic() - start, 20)
        left = subprocess.run([sandbox.docker_bin(), "ps", "-aq", "--filter", "name=code-fixer-"],
                              capture_output=True, text=True).stdout.split()
        self.assertEqual(left, [])

    def test_a_missing_image_is_a_sandbox_error(self):
        original = config.IMAGE
        config.IMAGE = "code-fixer-sandbox:does-not-exist"
        try:
            run = sandbox.run_tests(make_repo(self.root / "repo"), self.CMD)
        finally:
            config.IMAGE = original
        self.assertIsNotNone(run.error)
        self.assertIn("not built", run.error)


if __name__ == "__main__":
    unittest.main()
