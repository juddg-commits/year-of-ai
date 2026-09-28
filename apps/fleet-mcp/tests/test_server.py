"""Offline tests for the fleet MCP server: fake agents stand in for real ones, no API calls.
Run: .venv/bin/python -m unittest discover tests"""

import asyncio
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import server  # noqa: E402
from mcp.server.fastmcp.exceptions import ToolError  # noqa: E402

OK = {"agent": "fake-agent", "status": "ok", "output": "# Brief\nAll cited [S1].", "error": None,
      "cost_usd": 0.42, "seconds": 12.3, "artifacts": {"brief": "/runs/x.md", "trace": "/runs/x.json"}}


def fake_agent(stdout: str = "", stderr: str = "", sleep: float = 0, code: int = 0) -> list:
    """Args for `python -c` that behave like an agent CLI."""
    script = (f"import sys, time\nsys.stderr.write({stderr!r})\ntime.sleep({sleep})\n"
              f"sys.stdout.write({stdout!r})\nsys.exit({code})")
    return ["-c", script]


def run(args, **kw):
    return asyncio.run(server.run_agent(Path(tempfile.gettempdir()), args, python=sys.executable, **kw))


class RunAgent(unittest.TestCase):
    def test_result_is_the_last_stdout_line_and_progress_is_ignored(self):
        result = run(fake_agent(stdout="noise\n" + json.dumps(OK) + "\n", stderr="[1/6] Planning…\n[2/6] Researching…\n"))
        self.assertEqual(result, OK)

    def test_agent_error_becomes_a_tool_error_with_what_it_spent(self):
        failed = {**OK, "status": "error", "error": "Stopped: no evidence", "cost_usd": 0.7, "output": None}
        with self.assertRaisesRegex(ToolError, r"Stopped: no evidence \(spent \$0\.70\)"):
            run(fake_agent(stdout=json.dumps(failed), code=1))

    def test_crash_without_a_result_reports_the_last_output(self):
        with self.assertRaisesRegex(ToolError, "without a result(.|\n)*Traceback: boom"):
            run(fake_agent(stderr="[1/6] Planning…\nTraceback: boom\n", code=1))

    def test_a_stuck_agent_is_stopped_at_the_timeout(self):
        start = time.time()
        with self.assertRaisesRegex(ToolError, "ran past"):
            run(fake_agent(stdout=json.dumps(OK), sleep=30), timeout=0.5)
        self.assertLess(time.time() - start, 5)       # killed, not waited out

    def test_missing_venv_says_how_to_fix_it(self):
        with self.assertRaisesRegex(ToolError, "isn't installed"):
            asyncio.run(server.run_agent(Path("/nonexistent/some-agent"), ["x.py"]))


class Tools(unittest.TestCase):
    def test_research_enforces_the_cost_ceiling(self):
        seen = {}

        async def fake_run_agent(app, args, ctx=None):
            seen["args"] = args
            return OK

        with mock.patch.object(server, "run_agent", fake_run_agent):
            text = asyncio.run(server.research("Why?", ctx=None, sub_questions=10, searches=99))
        self.assertEqual(seen["args"], ["research.py", "--json", "--sub-questions", "4", "--searches", "3", "Why?"])
        self.assertTrue(text.startswith("fake-agent · $0.42 · 12s\nbrief: /runs/x.md\ntrace: /runs/x.json\n\n# Brief"))

    def test_the_client_never_sees_the_context_parameter(self):
        tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
        self.assertEqual(set(tools), {"research", "recent_research"})
        self.assertEqual(set(tools["research"].inputSchema["properties"]), {"question", "sub_questions", "searches"})

    def test_long_output_is_cut_and_points_to_the_file(self):
        text = server.as_text({**OK, "output": "x" * (server.MAX_OUTPUT_CHARS + 500)})
        self.assertIn("the full text is in /runs/x.md", text)
        self.assertLess(len(text), server.MAX_OUTPUT_CHARS + 300)

    def test_recent_research_lists_newest_first_and_skips_failed_runs(self):
        with tempfile.TemporaryDirectory() as d:
            runs = Path(d)
            (runs / "20260901-100000-older.json").write_text(json.dumps({"question": "Older?", "total_cost": 1.0}))
            (runs / "20260928-093154-newer.json").write_text(json.dumps({"question": "Newer?", "total_cost": 1.36}))
            (runs / "20260928-100000-FAILED.json").write_text(json.dumps({"question": "Broken?"}))
            listing = server.list_runs(runs, limit=10)
        self.assertEqual(listing.splitlines()[0], "- 2026-09-28 · $1.36 · Newer?")
        self.assertIn("Older?", listing)
        self.assertNotIn("Broken?", listing)


if __name__ == "__main__":
    unittest.main()
