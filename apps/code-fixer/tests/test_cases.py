"""Eval cases: loading, planting bugs, hiding tests, grading. The committed cases are checked
for consistency without running anything; eval.py verify runs them in the sandbox."""

import json
import tempfile
import unittest
from pathlib import Path

from code_fixer import cases, machine, workspace
from code_fixer.sandbox import TestRun

from tests.fakes import BUGGY, FIXED, TESTS, calc_runner


def case(**overrides):
    base = dict(id="calc-sign", source="calc", split="dev", kind="handwritten", category="flipped sign",
                note="adds with a minus", bug=[{"file": "calc.py", "find": "a + b", "replace": "a - b"}],
                hidden=["test_hidden.py"])
    return cases.Case(**{**base, **overrides})


class Materialize(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sources = Path(self.tmp.name) / "sources"
        src = self.sources / "calc"
        src.mkdir(parents=True)
        (src / "calc.py").write_text(FIXED)
        (src / "test_calc.py").write_text(TESTS)
        (src / "test_hidden.py").write_text("from calc import add\n\ndef test_more():\n    assert add(-1, 1) == 0\n")

    def tearDown(self):
        self.tmp.cleanup()

    def test_plants_the_bug_and_hides_the_hidden_tests(self):
        dest = cases.materialize(case(), Path(self.tmp.name) / "out", sources_dir=self.sources)
        self.assertEqual((dest / "calc.py").read_text(), BUGGY)
        self.assertFalse((dest / "test_hidden.py").exists())
        full = cases.materialize(case(), Path(self.tmp.name) / "full", hidden=True, sources_dir=self.sources)
        self.assertTrue((full / "test_hidden.py").exists())
        clean = cases.materialize(case(), Path(self.tmp.name) / "clean", bug=False, sources_dir=self.sources)
        self.assertEqual((clean / "calc.py").read_text(), FIXED)

    def test_the_bug_text_must_match_exactly_once(self):
        with self.assertRaisesRegex(ValueError, "occurs 0 times"):
            cases.materialize(case(bug=[{"file": "calc.py", "find": "a * b", "replace": "x"}]),
                              Path(self.tmp.name) / "o1", sources_dir=self.sources)
        with self.assertRaisesRegex(ValueError, "occurs 2 times"):
            cases.materialize(case(bug=[{"file": "calc.py", "find": "b", "replace": "x"}]),
                              Path(self.tmp.name) / "o2", sources_dir=self.sources)

    def test_verify_and_grade_with_a_fake_sandbox(self):
        runner = calc_runner()
        verdict = cases.verify(case(), runner=runner, sources_dir=self.sources)
        self.assertTrue(verdict["ok"], verdict["problems"])
        self.assertEqual(verdict["visible_failing"], ["test_calc.py::test_add"])
        self.assertTrue(cases.grade(case(), {"calc.py": FIXED}, runner=runner, sources_dir=self.sources).passed)
        self.assertFalse(cases.grade(case(), {}, runner=runner, sources_dir=self.sources).passed)
        # an edit to a test file never reaches the grader
        self.assertFalse(cases.grade(case(), {"test_calc.py": "def test_add(): pass\n"}, runner=runner,
                                     sources_dir=self.sources).passed)

    def test_verify_flags_a_bug_the_tests_miss(self):
        def always_passes(workspace, command):
            return TestRun(command, exit_code=0, outcomes={"test_calc.py::test_add": "passed"})
        verdict = cases.verify(case(), runner=always_passes, sources_dir=self.sources)
        self.assertFalse(verdict["ok"])
        self.assertIn("the bug doesn't fail any visible test", verdict["problems"])

    def test_hidden_ids(self):
        c = case(hidden=["tests/test_x.py", "tests/extra/"])
        self.assertTrue(c.is_hidden("tests/test_x.py::T::test_a"))
        self.assertTrue(c.is_hidden("tests/extra/test_y.py::test_b"))
        self.assertFalse(c.is_hidden("tests/test_xy.py::test_c"))


class PowerCheck(unittest.TestCase):
    def test_battery_and_a_closed_lid_block_a_paid_run(self):
        on_ac = "Now drawing from 'AC Power'\n -InternalBattery-0\t80%; charging"
        on_battery = "Now drawing from 'Battery Power'\n -InternalBattery-0\t4%; discharging"
        self.assertIn("battery", machine.power_problem_from(on_battery, '"AppleClamshellState" = No'))
        self.assertIn("lid is closed", machine.power_problem_from(on_ac, '  |   "AppleClamshellState" = Yes'))
        self.assertIsNone(machine.power_problem_from(on_ac, '  |   "AppleClamshellState" = No'))


class CommittedCases(unittest.TestCase):
    """The real cases in evals/: each one loads, and its bug and hidden files fit its source."""

    def test_every_case_is_consistent(self):
        found = cases.load()
        self.assertGreaterEqual(len(found), 5)
        for c in found:
            src = cases.SOURCES_DIR / c.source
            self.assertTrue((src / "LICENSE").exists() or c.kind == "handwritten", f"{c.id}: third-party source without a LICENSE")
            self.assertIn(c.split, ("dev", "test"))
            for edit in c.bug:
                self.assertFalse(workspace.is_protected(edit["file"]), c.id)
                self.assertEqual((src / edit["file"]).read_text().count(edit["find"]), 1, f"{c.id}: {edit['file']}")
            for rel in c.hidden:
                self.assertTrue((src / rel).exists(), f"{c.id}: {rel}")
                self.assertTrue(workspace.is_protected(rel), f"{c.id}: {rel}")

    def test_ids_match_file_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "one.json"
            bad.write_text(json.dumps({**cases.as_dict(case()), "id": "two"}))
            with self.assertRaisesRegex(ValueError, "doesn't match"):
                cases.load(cases_dir=Path(tmp))


if __name__ == "__main__":
    unittest.main()
