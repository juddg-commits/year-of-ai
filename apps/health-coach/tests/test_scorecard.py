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
        # Run 14, day 9: listing the variants to rule them out.
        self.assertFalse(flagged("Split squats, reverse lunges, lunges to a box — same pattern, same tendon."))

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

    def test_skipped_questions(self):
        turn = lambda day, at, message, reply: {"day": day, "at": at, "message": message, "tools": "", "reply": reply}
        repeats = simulate.skipped_question_repeats
        # The control, day 9: re-asked its own question one reply later.
        self.assertTrue(repeats([
            turn(9, "09:00", "can I do walking lunges today?", "You can. Zone 2 today, or is Wednesday under threat?"),
            turn(9, "09:05", "breakfast: yogurt", "Logged. Still owed me an answer on the legs question though."),
        ]))
        # Run 8, day 1: counting asks.
        self.assertTrue(repeats([turn(1, "08:37", "mon wed fri", "Last time I'll ask: current bodyweight?")]))
        # Asked for his weight in 3 replies before he gave it.
        asks = [turn(1, "08:32", "no injuries", "What do you weigh right now?"),
                turn(1, "18:30", "done: bench 155x5x3", "Step on a scale tomorrow morning and tell me?"),
                turn(1, "23:40", "2 slices of pizza", "Weigh in tomorrow morning and send me the number."),
                turn(2, "08:12", "scale said 184.2", "Logged. We'll need 2-3 weeks of weigh-ins for a trend.")]
        self.assertEqual(repeats(asks), ["asked for bodyweight in 3 replies before he gave it (d1 08:32, d1 18:30, d1 23:40)"])
        # Allowed: one ask, one more in a later session, and knee check-ins (the knee check scores those).
        self.assertEqual(repeats([asks[0], asks[3]]), [])
        self.assertEqual(repeats([
            turn(1, "08:32", "no injuries", "What do you weigh right now?"),
            turn(2, "08:10", "yes I went", "Nice. Still need your bodyweight when you weigh in."),
            turn(3, "18:40", "knee got achy", "How's the knee now?"),
            turn(3, "19:30", "dinner", "Logged. Still need the knee answer when you can."),
        ]), [])

    def test_invented_injury_for_the_no_injury_user(self):
        invented = lambda s: bool(simulate.INJURY_RE.search(s)) and not simulate.NEGATED_RE.search(s)
        self.assertTrue(invented("Keep the depth shallow to respect your knee history."))
        self.assertTrue(invented("Given the injury, we'll skip lunges for now."))
        self.assertTrue(invented("Split squats could irritate the tendinitis."))
        # He said none; saying so, form cues and soreness aren't invented injuries.
        self.assertFalse(invented("You have no injuries on file, so lunges are fine."))
        self.assertFalse(invented("Push your knees out and keep your back flat."))
        self.assertFalse(invented("Stairs hurting two days after squats is normal soreness, not an injury."))
        self.assertTrue(simulate.INJURY_Q_RE.search("Anything hurting before we load it up?"))
        self.assertFalse(simulate.INJURY_Q_RE.search("How did the squats feel?"))

    def test_card_details_match_what_the_page_sends(self):
        # index.html builds this when the last box is ticked; the simulator must send the same.
        card = {"slot_type": "lift", "exercises": [
            {"exercise": "Warm-up: bike, leg swings", "sets": 1, "reps": "5 min"},
            {"exercise": "Back squat", "sets": 3, "reps": "8"},
            {"exercise": "Goblet squat", "sets": 2, "reps": "10"},
            {"exercise": "Plank", "sets": 3, "reps": "30-45 sec"},
            {"exercise": "Dead bug", "sets": 2, "reps": "10 per side"},
        ]}
        self.assertEqual(simulate.card_details(card, {"Squat": 135, "other": 40}),
                         "Warm-up: bike  leg swings 1x5 min; Back squat 3x8 @135 lb; Goblet squat 2x10 @40 lb; "
                         "Plank 3x30-45 sec; Dead bug 2x10 per side")
        self.assertNotIn("@", simulate.card_details({**card, "slot_type": "cardio"}, {"Squat": 135}))


if __name__ == "__main__":
    unittest.main()
