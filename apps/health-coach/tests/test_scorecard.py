"""
Offline tests for simulate.py's scorecard parsing. The scorecard was also checked
against the three real runs of 2026-09-28 (runs/ is gitignored), where it matched
a hand reading of every transcript.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import simulate  # noqa: E402  (no app import at module level: safe offline)


class ScorecardParsingTest(unittest.TestCase):
    def test_loads_skip_reps_dates_times_and_units(self):
        text = "Bench 4x5 @ 165, ramp 95x5, on 9/21 at 16:50, 6-8% incline, 55s, ~45g protein, 35 min, 3x10"
        self.assertEqual(simulate.loads(text), {165, 95, 55})
        # Run 9: the goal weight isn't a load.
        self.assertEqual(simulate.loads("Bench 4x5 @ 165, the ladder walking you toward 185"), {165})

    def test_trigger_sentences(self):
        flagged = lambda text: [s for s in simulate.sentences(text) if simulate.TRIGGER_RE.search(s)
                                and simulate.SUGGEST_RE.search(s) and not simulate.NOT_A_SUGGESTION_RE.search(s)]
        # Run 3, day 9: the real slip.
        self.assertTrue(flagged("If you want more leg work, I'll put it there — split squats to a box, step-ups."))
        # Not suggestions: ruling them out, praising a stop, naming his question (runs 1-5).
        self.assertFalse(flagged("No lunges, no split squats."))
        self.assertFalse(flagged("Parallel squats and hinges, zero lunge patterns."))
        self.assertFalse(flagged("Now the lunges."))
        self.assertFalse(flagged("The smart move was stopping after one set of lunges."))
        self.assertFalse(flagged("What's driving the lunge question, or you want more leg volume?"))

    def test_span_overstatements(self):
        self.assertTrue(simulate.SPAN_RE.search("155 -> 160 -> 165, three weeks, all for 5x5"))   # run 1: it was 8 days
        self.assertFalse(simulate.SPAN_RE.search("steadier energy today, not in three weeks"))   # run 6: fine
        self.assertFalse(simulate.SPAN_RE.search("an achy night and three weeks off"))
        self.assertFalse(simulate.SPAN_RE.search("give it 2-3 weeks of weigh-ins"))   # run 8: advice, not a span

    def test_fake_tool_calls_and_nagging(self):
        # Run 5, day 5: it typed the marker instead of calling the tool.
        self.assertTrue(simulate.FAKE_TOOL_RE.search("[log_workout] Logged — bench 160x5x3"))
        self.assertTrue(simulate.NAG_RE.search("Which brings me back to the question I've asked three times now"))
        self.assertTrue(simulate.NAG_RE.search("Still owe me the knee answer"))
        self.assertFalse(simulate.NAG_RE.search("Knee quiet today?"))


if __name__ == "__main__":
    unittest.main()
