"""The eval's run command with every paid or slow part replaced: no API, no containers, no cost.
eval.py's top level only defines functions (main runs under __name__ == "__main__"), so importing it starts nothing."""

import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

import eval as ev
from code_fixer.sandbox import TestRun

from tests.fakes import FIXED, TESTS


def args(**over):
    base = dict(split="dev", cases=None, model="claude-opus-5-5", effort="medium", max_usd=0.50,
                max_total=None, no_test_loop=False, yes=True, ignore_power=False)
    return argparse.Namespace(**{**base, **over})


class Run(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ran = []
        self.cost = 0.30
        cases = [NS(id=f"case-{i}") for i in range(1, 6)]

        def fake_run_case(c, a, out_dir):
            self.ran.append((c.id, a.no_test_loop))
            return {"id": c.id, "solved": True, "api_failure": False, "cost": self.cost, "seconds": 1.0, "stop": "done"}

        patches = [
            mock.patch.object(ev.cases, "load", return_value=cases),
            mock.patch.object(ev.llm, "load_dotenv"),
            mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test"}),
            mock.patch.object(ev.machine, "power_problem", return_value=None),
            mock.patch.object(ev.sandbox, "image_id", return_value="sha256:test"),
            mock.patch.object(ev, "fetch_sources", return_value=True),
            mock.patch.object(ev, "run_case", side_effect=fake_run_case),
            mock.patch.object(ev.config, "RUNS_DIR", Path(self.tmp.name)),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def run_eval(self, **over):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = ev.cmd_run(args(**over))
        results = list(Path(self.tmp.name).glob("eval-*/results.json"))
        return code, out.getvalue(), json.loads(results[0].read_text()) if results else None

    def test_the_run_ceiling_stops_before_a_case_that_could_pass_it(self):
        # $0.30 a case, $0.50 a case at most, $1.00 for the run: after two cases ($0.60), a third could reach $1.10
        code, out, res = self.run_eval(max_total=1.00)
        self.assertEqual(code, 0)
        self.assertEqual([c for c, _ in self.ran], ["case-1", "case-2"])
        self.assertEqual(res["summary"]["not_run"], ["case-3", "case-4", "case-5"])
        self.assertEqual(res["summary"]["solved"], 2)
        self.assertIn("at most $1.00 in total", out)
        self.assertIn("Not run (the run's ceiling)", out)

    def test_a_crashed_case_counts_at_its_ceiling(self):
        def crash_then_ok(c, a, out_dir):
            self.ran.append((c.id, a.no_test_loop))
            if c.id == "case-1":
                raise RuntimeError("grading broke after the money was spent")
            return {"id": c.id, "solved": True, "api_failure": False, "cost": 0.01, "seconds": 1.0, "stop": "done"}
        with mock.patch.object(ev, "run_case", side_effect=crash_then_ok):
            _, _, res = self.run_eval(max_total=1.00)
        # case-1 counts $0.50; case-2 starts ($0.50 + $0.50 = $1.00); case-3 would pass $1.00
        self.assertEqual([c for c, _ in self.ran], ["case-1", "case-2"])
        self.assertEqual(res["cases"][0]["stop"], "crash")

    def test_no_ceiling_runs_every_case(self):
        _, out, res = self.run_eval()
        self.assertEqual(len(self.ran), 5)
        self.assertEqual(res["summary"]["not_run"], [])
        self.assertIn("at most $2.50 in total", out)

    def test_a_ceiling_below_one_case_runs_nothing(self):
        code, out, res = self.run_eval(max_total=0.40)
        self.assertEqual(code, 1)
        self.assertEqual(self.ran, [])
        self.assertIsNone(res)

    def test_the_baseline_flag_reaches_every_case_and_the_results(self):
        _, out, res = self.run_eval(no_test_loop=True)
        self.assertTrue(all(flag for _, flag in self.ran))
        self.assertTrue(res["args"]["no_test_loop"])
        self.assertIn("no test loop", out)

    def api_failure_on(self, status):
        def failing(c, a, out_dir):
            self.ran.append((c.id, a.no_test_loop))
            return {"id": c.id, "solved": False, "api_failure": True, "api_status": status, "cost": 0.0,
                    "seconds": 1.0, "stop": "api_error", "error": f"BadRequestError (HTTP {status}): credit balance is too low"}
        return failing

    def test_an_error_no_retry_fixes_stops_the_run(self):
        # 2026-10-02: an empty credit balance failed every case with a 400; the run kept going case after case
        with mock.patch.object(ev, "run_case", side_effect=self.api_failure_on(400)):
            code, out, res = self.run_eval()
        self.assertEqual(self.ran, [("case-1", False)])
        self.assertEqual(res["summary"]["api_failures"], ["case-1"])
        self.assertEqual(res["summary"]["not_run"], ["case-2", "case-3", "case-4", "case-5"])
        self.assertEqual(res["summary"]["graded"], 0)
        self.assertIn("HTTP 400 isn't retried", out)

    def test_an_overload_does_not_stop_the_run(self):
        with mock.patch.object(ev, "run_case", side_effect=self.api_failure_on(529)):
            _, _, res = self.run_eval()
        self.assertEqual(len(self.ran), 5)
        self.assertEqual(res["summary"]["not_run"], [])

    def test_without_yes_nothing_runs(self):
        code, out, res = self.run_eval(yes=False)
        self.assertEqual(code, 1)
        self.assertEqual(self.ran, [])
        self.assertIn("Nothing was run", out)


class Regrade(unittest.TestCase):
    """Saved patches against new hidden tests: the real patch tool, a fake sandbox."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.sources, self.hidden_dir, self.runs = root / "sources", root / "hidden", root / "runs"
        (self.sources / "calc").mkdir(parents=True)
        (self.sources / "calc" / "calc.py").write_text(FIXED)
        (self.sources / "calc" / "test_calc.py").write_text(TESTS)
        (self.hidden_dir / "calc-sign").mkdir(parents=True)
        (self.hidden_dir / "calc-sign" / "test_ours.py").write_text("def test_ours():\n    pass\n")
        self.case = ev.cases.Case(id="calc-sign", source="calc", split="dev", kind="real", category="flipped sign",
                                  note="adds with a minus", hidden=["test_ours.py"],
                                  bug=[{"file": "calc.py", "find": "a + b", "replace": "a - b"}])

        def runner(workspace, command):
            # visible test: calc adds; the hidden one (only when it's there): no abs() shortcut
            text = (Path(workspace) / "calc.py").read_text()
            outcomes = {"test_calc.py::test_add": "passed" if "+ b" in text else "failed"}
            if (Path(workspace) / "test_ours.py").exists():
                outcomes["test_ours.py::test_ours"] = "failed" if "abs(" in text else "passed"
            failed = "failed" in outcomes.values()
            return TestRun(list(command), exit_code=1 if failed else 0, outcomes=outcomes)

        real_grade, real_patched = ev.cases.grade, ev.cases.patched_files
        patches = [
            mock.patch.object(ev.cases, "load", return_value=[self.case]),
            mock.patch.object(ev.cases, "grade", side_effect=lambda c, changed: real_grade(
                c, changed, runner=runner, sources_dir=self.sources, hidden_dir=self.hidden_dir)),
            mock.patch.object(ev.cases, "patched_files", side_effect=lambda c, diff: real_patched(
                c, diff, sources_dir=self.sources, hidden_dir=self.hidden_dir)),
            mock.patch.object(ev.sandbox, "image_id", return_value="sha256:test"),
            mock.patch.object(ev, "fetch_sources", return_value=True),
            mock.patch.object(ev.config, "RUNS_DIR", self.runs),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def saved_run(self, name, rows, patches):
        run = self.runs / name
        run.mkdir(parents=True)
        (run / "results.json").write_text(json.dumps({"args": {"split": "dev", "model": "m"}, "cases": rows}))
        for case_id, new_line in patches.items():
            (run / f"{case_id}.patch").write_text(
                "--- a/calc.py\n+++ b/calc.py\n@@ -1,2 +1,2 @@\n def add(a, b):\n-    return a - b\n" + new_line)
        return run

    def regrade(self, *runs):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = ev.cmd_regrade(argparse.Namespace(runs=[str(r) for r in runs], cases=None, split=None))
        return code, out.getvalue(), json.loads(next(self.runs.glob("regrade-*/results.json")).read_text())

    def test_a_patch_the_new_hidden_test_catches_is_no_longer_solved(self):
        row = {"id": "calc-sign", "solved": True, "visible_pass": True, "api_failure": False, "stop": "done"}
        good = self.saved_run("eval-good", [row], {"calc-sign": "+    return a + b\n"})
        sly = self.saved_run("eval-sly", [row], {"calc-sign": "+    return abs(a) + b\n"})
        code, printed, saved = self.regrade(good, sly)
        self.assertEqual(code, 0)
        by_run = {r["run"]: r for r in saved["runs"]}
        self.assertEqual((by_run["eval-good"]["solved_before"], by_run["eval-good"]["solved_now"]), (1, 1))
        self.assertEqual((by_run["eval-sly"]["solved_before"], by_run["eval-sly"]["solved_now"]), (1, 0))
        self.assertEqual(by_run["eval-sly"]["now_failing"], ["calc-sign"])
        sly_row = next(r for r in saved["rows"] if r["run"] == "eval-sly")
        self.assertEqual(sly_row["hidden_failing"], ["test_ours.py::test_ours"])
        self.assertIn("now failing: calc-sign", printed)

    def test_no_patch_an_api_failure_and_a_patch_that_doesnt_apply(self):
        rows = [{"id": "calc-sign", "solved": False, "visible_pass": False, "api_failure": False, "stop": "budget"}]
        unpatched = self.saved_run("eval-none", rows, {})
        failed = self.saved_run("eval-api", [{**rows[0], "api_failure": True}], {"calc-sign": "+    return a + b\n"})
        stale = self.runs / "eval-stale"
        stale.mkdir()
        (stale / "results.json").write_text(json.dumps({"args": {}, "cases": [{**rows[0], "solved": True, "visible_pass": True}]}))
        (stale / "calc-sign.patch").write_text("--- a/calc.py\n+++ b/calc.py\n@@ -1,1 +1,1 @@\n-def mul(a, b):\n+def times(a, b):\n")
        code, printed, saved = self.regrade(unpatched, failed, stale)
        self.assertEqual(code, 1)   # an error is reported, never scored
        rows = {r["run"]: r for r in saved["rows"]}
        self.assertNotIn("eval-api", rows)
        self.assertFalse(rows["eval-none"]["solved_now"])
        self.assertIsNone(rows["eval-none"]["error"])
        self.assertIn("doesn't apply", rows["eval-stale"]["error"])
        self.assertIn("errors: calc-sign", printed)


if __name__ == "__main__":
    unittest.main()
