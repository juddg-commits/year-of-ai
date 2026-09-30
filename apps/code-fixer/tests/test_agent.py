"""The fix loop with a scripted model and a fake sandbox: no API calls, no containers, no cost.
Run: .venv/bin/python -m unittest discover tests"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

from code_fixer import agent, config, llm, report
from code_fixer.agent import Limits, fix, unresolved_tests
from code_fixer.sandbox import TestRun

from tests.fakes import (BUGGY, FIXED, TESTS, APIStatusError, FakeClient, calc_runner, make_repo, response, text,
                    thinking, tool, usage)

CMD = ["python", "-m", "pytest", "-q"]
EDIT = {"path": "calc.py", "old_string": "return a - b", "new_string": "return a + b"}


class Loop(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = make_repo(Path(self.tmp.name) / "repo")

    def tearDown(self):
        self.tmp.cleanup()

    def run_fix(self, script, limits=None, runner=None):
        self.client = FakeClient(script)
        return fix(self.repo, CMD, limits=limits or Limits(), client=self.client, runner=runner or calc_runner())

    def test_a_fix_is_made_checked_and_returned_as_a_patch(self):
        res = self.run_fix([
            response(thinking("The subtraction looks wrong."), tool("read_file", {"path": "calc.py"})),
            response(tool("edit_file", EDIT)),
            response(tool("run_tests", {})),
            response(text("add() subtracted. It adds now.")),
        ])
        self.assertTrue(res.fixed)
        self.assertEqual(res.stop, "done")
        self.assertEqual(res.summary, "add() subtracted. It adds now.")
        self.assertIn("-    return a - b\n+    return a + b\n", res.diff)
        self.assertEqual(res.changed, {"calc.py": FIXED})
        self.assertEqual((res.turns, res.tool_calls, res.test_runs), (4, 3, 1))
        self.assertEqual(res.unresolved, [])
        self.assertEqual((self.repo / "calc.py").read_text(), BUGGY)   # the original is never touched
        self.assertAlmostEqual(res.cost, 4 * llm.cost_of(config.MODEL, 100, 200, 0, 0))
        self.assertEqual(res.trace["turns"][0]["thinking"], ["The subtraction looks wrong."])

    def test_the_conversation_is_append_only(self):
        """Opus 5.5 binds its thinking to the exact prefix: system, tools and every earlier message
        must be identical on every call, with each response appended unchanged."""
        first = response(thinking("t1"), tool("read_file", {"path": "calc.py"}))
        second = response(tool("edit_file", EDIT))
        self.run_fix([first, second, response(text("Done."))])
        calls = self.client.requests
        self.assertEqual(len(calls), 3)
        for before, after in zip(calls, calls[1:]):
            self.assertEqual(after["messages"][:len(before["messages"])], before["messages"])
            self.assertIs(after["system"], before["system"])
            self.assertIs(after["tools"], before["tools"])
            self.assertEqual(after["output_config"], before["output_config"])
        self.assertEqual(calls[1]["messages"][1], {"role": "assistant", "content": first.content})
        self.assertEqual(calls[2]["messages"][3], {"role": "assistant", "content": second.content})
        results = calls[1]["messages"][2]["content"]
        self.assertEqual([r["tool_use_id"] for r in results], [first.content[1].id])
        self.assertNotIn("tool_choice", calls[0])   # forced tool choice is a 400 on Opus 5.5
        self.assertEqual(calls[0]["thinking"]["type"], "adaptive")
        self.assertEqual(calls[0]["fallbacks"], "default")

    def test_saying_it_is_fixed_is_not_a_fix(self):
        res = self.run_fix([response(text("Fixed it!"))])
        self.assertFalse(res.fixed)
        self.assertEqual(res.stop, "done")
        self.assertEqual(res.diff, "")
        self.assertEqual(res.unresolved, ["test_calc.py::test_add"])

    def test_edits_to_tests_are_refused_and_logged(self):
        res = self.run_fix([
            response(tool("edit_file", {"path": "test_calc.py", "old_string": "== 5", "new_string": "== -1"})),
            response(tool("edit_file", EDIT)),
            response(text("Fixed add().")),
        ])
        self.assertTrue(res.fixed)
        first_result = self.client.requests[1]["messages"][2]["content"][0]
        self.assertTrue(first_result["is_error"])
        self.assertIn("read-only", first_result["content"])
        self.assertEqual(res.trace["refused_edits"][0]["path"], "test_calc.py")
        self.assertNotIn("test_calc.py", res.diff)

    def test_the_final_check_runs_on_a_fresh_copy_with_tests_restored(self):
        seen = []
        res = self.run_fix([response(tool("edit_file", EDIT)), response(text("ok"))], runner=calc_runner(seen))
        self.assertTrue(res.fixed)
        self.assertEqual([p.name for p in seen], ["work", "check"])
        self.assertIsNone(res.trace.get("protected_files_changed"))

    def test_nothing_to_fix_makes_no_api_call(self):
        (self.repo / "calc.py").write_text(FIXED)
        res = self.run_fix([])
        self.assertEqual(res.stop, "nothing_to_fix")
        self.assertEqual(self.client.requests, [])
        self.assertEqual(res.cost, 0)

    def test_the_dollar_ceiling_stops_the_next_call(self):
        # The first reply costs about $0.09 of a $0.10 ceiling: the next call can't be paid for.
        pricey = usage(inp=5_000, out=3_500)   # 5k x $4 + 3.5k x $20 per million = $0.09
        res = self.run_fix([response(tool("read_file", {"path": "calc.py"}), u=pricey)], limits=Limits(max_usd=0.10))
        self.assertEqual(res.stop, "budget")
        self.assertEqual(len(self.client.requests), 1)
        self.assertLessEqual(res.cost, 0.10)
        affordable = llm.affordable_max_tokens(config.MODEL, 0.10, 0, 1)
        self.assertLessEqual(self.client.requests[0]["max_tokens"], affordable)

    def test_the_output_limit_shrinks_to_fit_the_budget(self):
        self.run_fix([response(text("Nothing found."))], limits=Limits(max_usd=0.20))
        self.assertLess(self.client.requests[0]["max_tokens"], config.MAX_TOKENS)
        self.assertGreaterEqual(self.client.requests[0]["max_tokens"], config.MIN_TOKENS)

    def test_an_api_error_is_logged_and_the_work_is_still_checked(self):
        res = self.run_fix([response(tool("edit_file", EDIT)), APIStatusError("overloaded", 529)])
        self.assertEqual(res.stop, "api_error")
        self.assertEqual(res.trace["api_error"]["status"], 529)
        self.assertIn("APIStatusError (HTTP 529): overloaded", res.error)
        self.assertTrue(res.fixed)   # the edit before the failure passed the final check

    def test_a_refusal_stops_the_loop(self):
        res = self.run_fix([response(stop="refusal")])
        self.assertEqual(res.stop, "refused")
        self.assertFalse(res.fixed)

    def test_every_call_gets_a_result_when_the_tool_limit_hits(self):
        calls = [tool("read_file", {"path": "calc.py"}) for _ in range(3)]
        res = self.run_fix([response(*calls)], limits=Limits(max_tool_calls=2))
        self.assertEqual(res.stop, "max_tool_calls")
        self.assertEqual(res.tool_calls, 2)
        self.assertEqual([t.get("not_run") for t in res.trace["turns"][0]["tools"]], [None, None, "tool-call limit"])

    def test_a_cut_off_tool_call_is_not_run(self):
        res = self.run_fix([
            response(tool("edit_file", {"path": "calc.py", "old_string": "return a - b", "new_string": "return a +"}),
                     stop="max_tokens"),
            response(text("I'll stop here.")),
        ])
        self.assertEqual(res.tool_calls, 0)
        self.assertFalse(res.fixed)
        result = self.client.requests[1]["messages"][2]["content"][0]
        self.assertTrue(result["is_error"])
        self.assertIn("output limit", result["content"])

    def test_test_runs_are_limited(self):
        res = self.run_fix([response(tool("run_tests", {})), response(tool("run_tests", {})),
                            response(text("Out of runs."))], limits=Limits(max_test_runs=1))
        self.assertEqual(res.test_runs, 1)
        second = self.client.requests[2]["messages"][4]["content"][0]
        self.assertIn("no test runs left", second["content"])

    def test_a_command_that_collects_no_tests_makes_no_api_call(self):
        def nothing_collected(workspace, command):
            return TestRun(command, exit_code=5, output="no tests ran in 0.01s")
        res = self.run_fix([], runner=nothing_collected)
        self.assertEqual(res.stop, "bad_command")
        self.assertEqual(self.client.requests, [])
        self.assertIn("pytest exit 5", res.error)

    def test_the_sandbox_failing_up_front_stops_the_run(self):
        def broken(workspace, command):
            return TestRun(command, error="docker not found")
        res = self.run_fix([], runner=broken)
        self.assertEqual(res.stop, "sandbox_error")
        self.assertEqual(res.error, "docker not found")

    def test_a_crash_in_the_loop_keeps_the_work_and_the_trace(self):
        res = self.run_fix([response(tool("edit_file", EDIT)), ValueError("boom")])
        # a non-API exception from the client is still an API-call failure: logged, not raised
        self.assertEqual(res.stop, "api_error")
        self.assertIn("ValueError: boom", res.error)
        self.assertTrue(res.fixed)


class Unresolved(unittest.TestCase):
    def run_of(self, outcomes, exit_code=1):
        return TestRun(["pytest"], exit_code=exit_code, outcomes=outcomes)

    def test_every_test_that_ran_must_pass_after(self):
        before = self.run_of({"t.py::a": "passed", "t.py::b": "failed", "t.py::c": "xfailed"})
        after = self.run_of({"t.py::a": "passed", "t.py::b": "passed", "t.py::c": "xfailed"}, 0)
        self.assertEqual(unresolved_tests(before, after), [])

    def test_a_test_that_disappears_is_unresolved(self):
        before = self.run_of({"t.py::a": "passed", "t.py::b": "failed"})
        after = self.run_of({"t.py::b": "passed"}, 0)
        self.assertEqual(unresolved_tests(before, after), ["t.py::a"])

    def test_a_collection_error_is_resolved_when_the_file_runs_clean(self):
        before = self.run_of({"t.py": "error", "u.py::x": "passed"}, 2)
        after = self.run_of({"t.py::a": "passed", "u.py::x": "passed"}, 0)
        self.assertEqual(unresolved_tests(before, after), [])
        self.assertEqual(unresolved_tests(before, self.run_of({"u.py::x": "passed"}, 0)), ["t.py"])


class Ledger(unittest.TestCase):
    def test_opus_5_5_prices_with_its_cheaper_cache_reads(self):
        ledger = llm.Ledger()
        rec = ledger.record(1, "claude-opus-5-5", NS(model="claude-opus-5-5", stop_reason="end_turn",
                            usage=usage(inp=1000, out=2000, read=10_000, write=3000)), 1.0, 8000)
        # 1k x $4 + 2k x $20 + 3k x $5 (write) + 10k x $0.20 (read), per million
        self.assertAlmostEqual(rec.cost, (4000 + 40_000 + 15_000 + 2000) / 1e6)
        self.assertEqual(rec.prompt_tokens, 14_000)

    def test_a_fallback_is_billed_per_attempt_at_each_models_rates(self):
        iterations = [NS(type="message", input_tokens=500, output_tokens=0, cache_read_input_tokens=0,
                         cache_creation_input_tokens=0),
                      NS(type="fallback_message", model="claude-opus-5", input_tokens=500, output_tokens=1000,
                         cache_read_input_tokens=0, cache_creation_input_tokens=0)]
        resp = NS(model="claude-opus-5", stop_reason="end_turn", usage=usage(iterations=iterations))
        rec = llm.Ledger().record(1, "claude-opus-5-5", resp, 1.0, 8000)
        self.assertAlmostEqual(rec.cost, 500 * 4 / 1e6 + (500 * 5 + 1000 * 25) / 1e6)
        self.assertTrue(rec.fallback)
        self.assertEqual(rec.model, "claude-opus-5")

    def test_an_unknown_model_is_priced_high_not_low(self):
        self.assertEqual(llm.prices("claude-something-new"), config.PRICES["claude-opus-5"])
        self.assertEqual(llm.prices("claude-opus-5-5"), config.PRICES["claude-opus-5-5"])
        self.assertEqual(llm.prices("claude-opus-5"), config.PRICES["claude-opus-5"])

    def test_affordable_output_after_paying_for_the_input(self):
        n = llm.affordable_max_tokens("claude-opus-5-5", 0.50, cached_tokens=10_000, new_tokens=2000)
        input_cost = 1.15 * (10_000 * 0.20 + 2000 * 5.00) / 1e6
        self.assertEqual(n, int((0.50 - input_cost) * 1e6 / 20))
        self.assertEqual(llm.affordable_max_tokens("claude-opus-5-5", 0.001, 100_000, 0), 0)

    def test_echoing_a_fallback_turn_drops_the_declined_partial(self):
        declined = [thinking("x"), tool("read_file", {"path": "a"}), text("partial")]
        boundary = NS(type="fallback")
        served = [thinking("y"), tool("read_file", {"path": "b"})]
        echoed = llm.echo_content(declined + [boundary] + served)
        self.assertEqual(echoed, [declined[2], boundary] + served)
        self.assertEqual(llm.echo_content(served), served)


class Report(unittest.TestCase):
    def test_headlines(self):
        before = TestRun(["pytest"], exit_code=1, outcomes={"t::a": "failed", "t::b": "passed"})
        after = TestRun(["pytest"], exit_code=0, outcomes={"t::a": "passed", "t::b": "passed"})
        ok = agent.FixResult("r", ["pytest"], "m", fixed=True, stop="done", before=before, after=after)
        self.assertEqual(report.headline(ok), "Fixed. After: 2 passed (exit 0). Before: 1 failed, 1 passed (exit 1).")
        bad = agent.FixResult("r", ["pytest"], "m", stop="budget", before=before, after=before, unresolved=["t::a"])
        self.assertEqual(report.headline(bad), "Not fixed (the dollar ceiling was reached). "
                                               "After: 1 failed, 1 passed (exit 1). Still not passing: t::a.")

    def test_every_run_saves_a_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            res = agent.FixResult("/x/my repo.v2", ["pytest"], "m", stop="api_error", trace={"stop": "api_error"})
            paths = report.save(res, Path(tmp))
            self.assertTrue(paths["trace"].endswith("-my-repo.v2-NOT-FIXED.json"), paths["trace"])
            self.assertTrue(Path(paths["trace"]).exists())
            self.assertNotIn("patch", paths)


if __name__ == "__main__":
    unittest.main()
