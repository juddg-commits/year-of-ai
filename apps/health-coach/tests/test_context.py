"""
Offline tests for what the coach sees: the dated, pre-computed context the
10-day simulation showed it needed, the cache layout, and the cost trace.
No API calls: a fake client stands in for Claude, so these run free.

    .venv/bin/python -m unittest discover tests
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent.parent
DATA_DIR = Path(tempfile.mkdtemp(prefix="coach-test-"))
# Set before importing: coach.py reads DATA_DIR at import time. A fake key means
# a call that slips past the fakes fails instead of spending money.
os.environ["DATA_DIR"] = str(DATA_DIR)
os.environ["ANTHROPIC_API_KEY"] = "test-no-calls"
os.environ["APP_PASSWORD"] = ""
os.environ["YOUTUBE_API_KEY"] = ""
sys.path.insert(0, str(HERE))

import coach  # noqa: E402
import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

assert coach.DATA.resolve() == DATA_DIR.resolve(), "tests must never touch the real data/ folder"


class FakeDate(date):
    """Wednesday 2026-09-23, day 10 of the simulation."""
    day = (2026, 9, 23)

    @classmethod
    def today(cls):
        return cls(*cls.day)


coach.date = app.date = FakeDate

# The simulation's own logs (runs/sim-20260928-155554), the data the bugs showed up on.
WEIGHTS = """# Weight Log
- **2026-09-15** — 184.2 lb
- **2026-09-18** — 183 lb
- **2026-09-20** — 184.6 lb
- **2026-09-23** — 182.8 lb
"""
MEALS = """# Food Log
- **2026-09-14 12:30** — Large dining hall chicken and rice bowl: ~700 kcal, ~45g protein
- **2026-09-14 23:40** — 2 slices pepperoni pizza (late night): ~600 kcal, ~26g protein
- **2026-09-15 08:20** — 3 eggs + 2 slices toast: ~400 kcal, ~24g protein
- **2026-09-16 19:30** — 2 chicken breasts, rice, broccoli: ~650 kcal, ~75g protein
- **2026-09-17 13:00** — 3 slices pepperoni pizza + Monster energy drink: ~1060 kcal, ~39g protein
- **2026-09-20 11:00** — 6 beers + late-night burrito (2am): ~1850 kcal, ~35g protein
- **2026-09-22 09:05** — Greek yogurt with granola and a banana: ~430 kcal, ~22g protein
"""
WORKOUTS = """# Training Log
- **2026-09-14** — Upper body (push/pull): Bench 155x5x3; barbell row 125x8x3 (effort 7/10)
- **2026-09-16** — Lower body: Squat 185x5x3; walking lunges 1 set (stopped — left knee ache) (effort 8/10)
- **2026-09-18** — Upper body (push/pull): Bench 160x5x3 (up from 155) (effort 8/10)
- **2026-09-21** — Upper body (push/pull): Bench 165x5x3 (effort 7/10)
"""
CARD = {
    "title": "Upper A — Bench Focus", "focus": "Horizontal press", "duration_minutes": 55,
    "slot_type": "lift", "date": "2026-09-23",
    "exercises": [{"exercise": "Barbell Bench Press", "sets": 4, "reps": "5", "rest_seconds": 180,
                   "notes": "Today: 165 for 4 sets of 5.", "video_query": ""}],
}
PROGRAM = {
    "week_focus": "Build the streak", "rationale": "r", "step_target": 9000, "week": "2026-W39",
    "days": [{"day": d, "type": "lift" if d == "Wednesday" else "rest",
              "title": "Lower A — knee-safe" if d == "Wednesday" else "Rest",
              "detail": "Squat to parallel. No lunges." if d == "Wednesday" else "walk",
              "duration_minutes": 60, "video_query": ""} for d in app.DAY_NAMES],
}


def usage(inp=1000, out=100, cache_write=0, cache_read=0):
    return SimpleNamespace(input_tokens=inp, output_tokens=out,
                           cache_creation_input_tokens=cache_write, cache_read_input_tokens=cache_read)


class FakeMessages:
    """Records every create() call and answers with a fixed reply."""
    def __init__(self, text: str):
        self.text, self.calls = text, []

    def create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.text)],
                               stop_reason="end_turn", usage=usage(cache_read=4000))


class ContextTest(unittest.TestCase):
    def setUp(self):
        FakeDate.day = (2026, 9, 23)
        shutil.rmtree(DATA_DIR, ignore_errors=True)
        DATA_DIR.mkdir(parents=True)
        (DATA_DIR / "weight.md").write_text(WEIGHTS)
        (DATA_DIR / "meals.md").write_text(MEALS)
        (DATA_DIR / "workouts.md").write_text(WORKOUTS)
        app.workout_cache.clear()
        app.history.clear()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(DATA_DIR, ignore_errors=True)

    # ── Dates ─────────────────────────────────────────────────────────────────
    def test_dates_carry_weekday_and_age(self):
        self.assertEqual(coach.dated("2026-09-14"), "Mon 2026-09-14 (9 days ago)")
        self.assertEqual(coach.dated("2026-09-22"), "Tue 2026-09-22 (yesterday)")
        self.assertEqual(coach.dated("2026-09-23"), "Wed 2026-09-23 (today)")
        self.assertEqual(coach.dated("2026-13-45"), "2026-13-45")   # not a date: left alone

    def test_training_log_is_dated(self):
        # The day-3 card said "last Friday's session" about a Monday one.
        self.assertIn("Mon 2026-09-14 (9 days ago)", coach.load_recent_workouts())

    def test_session_logs_carry_their_date(self):
        # The date only lived in the file name, so the model couldn't tell days apart.
        (DATA_DIR / "log").mkdir()
        (DATA_DIR / "log" / "2026-09-22.md").write_text("\n### Session 09:05\n- **Judd:** breakfast: yogurt")
        logs = coach.load_recent_logs()
        self.assertIn("### Tue 2026-09-22 (yesterday)", logs)
        self.assertIn("#### Session 09:05", logs)

    def test_saved_sessions_never_show_tool_markers(self):
        # Run 5: logs read "[log_workout] Logged…", and the coach copied the marker
        # as text instead of calling the tool. New logs leave tools out; old ones get cleaned.
        history = [{"role": "user", "content": "bench 160x5x3"},
                   {"role": "assistant", "content": [SimpleNamespace(type="tool_use", name="log_workout"),
                                                     SimpleNamespace(type="text", text="Logged: 160x5x3.")]}]
        coach.save_session(history)
        (DATA_DIR / "log" / "2026-09-22.md").write_text("\n### Session 18:30\n- **Coach:** [log_weight] 183.0 logged.")
        saved = (DATA_DIR / "log" / "2026-09-23.md").read_text()
        self.assertIn("- **Coach:** Logged: 160x5x3.", saved)
        self.assertNotIn("[log_", saved + coach.load_recent_logs())
        self.assertIn("- **Coach:** 183.0 logged.", coach.load_recent_logs())

    def test_saved_session_says_when_he_never_answered(self):
        # Day 10 of run 2 said "yesterday we locked yogurt + banana"; he never replied.
        history = [{"role": "user", "content": "breakfast: yogurt"},
                   {"role": "assistant", "content": "Same yogurt tomorrow? Sound doable?"}]
        coach.save_session(history)
        coach.save_session(history, ended=False)   # a mid-conversation rollover: he's still talking
        log = (DATA_DIR / "log" / "2026-09-23.md").read_text()
        self.assertEqual(log.count("he didn't reply to the coach's last message"), 1)

    def test_notes_get_their_own_section(self):
        # Run 3: a goals note was appended into the Safety section, so intake
        # never saw a Goals section and kept asking.
        (DATA_DIR / "profile.md").write_text("# Judd's Profile\n\n## Safety\n- knee: patellar tendinitis")
        result = coach.execute_tool("save_note", {"note": "Goals: -10 lb by winter break, bench 185."})
        coach.execute_tool("save_note", {"note": "Lunges flared the knee again."})
        profile = (DATA_DIR / "profile.md").read_text()
        safety = profile.split("## Safety\n")[1].split("\n## ")[0]
        self.assertEqual(safety.strip(), "- knee: patellar tendinitis")
        self.assertIn("## Notes the coach saved\n- (2026-09-23) Goals:", profile)
        self.assertEqual(profile.count("## Notes the coach saved"), 1)
        self.assertIn("If the profile or notes already cover one, save that section now", result)

    # ── Computed facts ────────────────────────────────────────────────────────
    def test_weight_summary_states_the_real_change(self):
        # Day 10's true answer, which the coach couldn't give: -1.4 lb over 8 days.
        s = coach.load_weight_summary()
        self.assertIn("184.2 -> 182.8 lb = -1.4 lb over 8 days (4 weigh-ins)", s)
        self.assertIn("Range 182.8-184.6 lb", s)
        self.assertIn("Average of the last 7 days: 183.5 lb (3 weigh-ins); the 7 days before: 184.2 lb (1)", s)
        self.assertIn("too early to call a trend", s)

    def test_food_summary_covers_past_days(self):
        # Day 10 said "I genuinely can't tell you" with 7 meals on file.
        s = coach.load_food_summary()
        self.assertIn("Wed 2026-09-23 (today): nothing logged", s)
        self.assertIn("Tue 2026-09-22 (yesterday): 1 meal(s), ~430 kcal, ~22 g protein", s)
        self.assertIn("reached on 0 of 3 logged days; best was ~39 g on Thu 2026-09-17", s)
        self.assertNotIn("2026-09-16", s)   # outside the 7-day window

    def test_protein_by_day_sums_each_day(self):
        self.assertEqual(coach.protein_by_day()["2026-09-14"], 71)
        self.assertEqual(coach.protein_by_day()["2026-09-16"], 75)

    def test_lift_history_reads_both_ways_he_logs(self):
        # The simulation always typed "155x5x3"; the real app's log says "Bench 3x8 @140 lb"
        # (sets x reps @ weight), which the history first read as nothing at all. Made-up numbers.
        (DATA_DIR / "workouts.md").write_text(
            "# Training Log\n"
            "- **2026-09-10** — push day: Bench 3x8 @140 lb; incline DB press 3x10 @45s (effort 7/10)\n"
            "- **2026-09-14** — Upper A — bench focus: Bench 155x5x3; barbell row 125x8x3\n"
            "- **2026-09-16** — Lower: Squat 3x5 @ RPE 8; RDL 3x8 @ 70%\n"   # no weight in either
            "- **2026-09-18** — Upper A: Barbell Bench Press 4x5; Barbell row 3x8 at 130\n")   # the Complete button's format
        h = coach.lift_history()
        self.assertEqual(h["Bench press"], [("2026-09-10", 140, 8), ("2026-09-14", 155, 5)])
        self.assertEqual(h["Incline press"], [("2026-09-10", 45, 10)])
        self.assertEqual(h["Row"], [("2026-09-14", 125, 8), ("2026-09-18", 130, 8)])
        self.assertNotIn("Squat", h)
        self.assertNotIn("RDL", h)

    def test_workout_card_logs_the_weight_used(self):
        # The page builds this when the last box is ticked, with the weights he typed.
        # It used to send "Barbell Bench Press 4x5" and the coach never saw a load.
        details = ("Warm-up: easy bike 1x5 min; Barbell Bench Press 4x5 @165 lb; Barbell row 3x8-10 @135 lb; "
                   "Front squat 3x6 @135 lb; Bulgarian split squat 3x10 each leg @40 lb; "
                   "Dumbbell bench press 3x10 @60 lb; Chest-supported row 3x10 @45 lb; "
                   "Lat pulldown 3x10 each set @120 lb; Romanian deadlift 3x8")   # no weight typed
        r = TestClient(app.app).post("/workout/complete", json={"title": "Upper A — Bench Focus", "details": details},
                                     headers={"X-Coach": "1"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("Barbell Bench Press 4x5 @165 lb", (DATA_DIR / "workouts.md").read_text())
        h = coach.lift_history()
        self.assertEqual(h["Bench press"][-1], ("2026-09-23", 165, 5))    # not the 60 lb dumbbells
        self.assertEqual(h["Row"][-1], ("2026-09-23", 135, 8))            # bottom of 8-10; not the 45 lb row
        self.assertEqual(h["Squat"], [("2026-09-16", 185, 5)])            # front and split squats aren't his squat
        self.assertEqual(h["Lat pulldown"], [("2026-09-23", 120, 10)])
        self.assertNotIn("RDL", h)
        self.assertIn("Squat: 185x5 on Wed 2026-09-16 (7 days ago); 1 session logged", coach.load_lift_history())

    # ── Prompt layout and caching ─────────────────────────────────────────────
    def test_prompt_blocks_go_from_frozen_to_volatile(self):
        (DATA_DIR / "profile.md").write_text("# Judd's Profile\n\n## Injuries\n- left knee")
        blocks = coach.build_system_prompt()
        self.assertEqual(len(blocks), 3)
        self.assertEqual(blocks[0]["text"], coach.COACH_INSTRUCTIONS)
        self.assertIn("cache_control", blocks[0])
        self.assertIn("left knee", blocks[1]["text"])
        self.assertIn("cache_control", blocks[1])
        self.assertTrue(blocks[2]["text"].startswith("Today is Wednesday, 2026-09-23."))
        self.assertNotIn("cache_control", blocks[2])
        for heading in ("## Recent training log", "## Weight log", "## Food log, last 7 days"):
            self.assertIn(heading, blocks[2]["text"])

    def test_cached_blocks_do_not_change_from_day_to_day(self):
        # Any byte that changes in a cached block turns every call into a cache miss.
        (DATA_DIR / "profile.md").write_text("# Judd's Profile\n\n## Injuries\n- left knee")
        monday = coach.build_system_prompt()
        FakeDate.day = (2026, 9, 24)
        thursday = coach.build_system_prompt()
        self.assertEqual(monday[:2], thursday[:2])
        self.assertNotIn("2026-09-2", coach.COACH_INSTRUCTIONS)

    # ── Today's session in chat ───────────────────────────────────────────────
    def test_chat_context_shows_todays_card(self):
        # Day 8: the chat gave different weights than the card on the Workout tab.
        app.workout_cache["2026-09-23"] = CARD
        ctx = app.todays_session_context()
        self.assertIn("Barbell Bench Press: 4 x 5. Today: 165 for 4 sets of 5.", ctx)
        self.assertIn("Today's session (what his Workout tab shows)", coach.build_system_prompt(ctx)[2]["text"])

    def test_no_card_falls_back_to_the_program_slot(self):
        self.assertEqual(app.todays_session_context(), "")
        app.PROGRAM_FILE.write_text(json.dumps(PROGRAM))
        ctx = app.todays_session_context()
        self.assertIn("No session card generated yet today", ctx)
        self.assertIn("lift, Lower A — knee-safe: Squat to parallel. No lunges.", ctx)
        self.assertIn("This week: Mon rest (Rest)", ctx)

    def test_bad_program_file_costs_the_section_not_the_turn(self):
        app.PROGRAM_FILE.write_text(json.dumps({"week": "2026-W39"}))   # no "days"
        self.assertEqual(app.todays_session_context(), "")

    def test_chat_request_carries_card_and_cache_control(self):
        fake = FakeMessages("Bench is 165 today.")
        app.client = SimpleNamespace(messages=fake)
        app.workout_cache["2026-09-23"] = CARD
        reply = TestClient(app.app, headers={"X-Coach": "1"}).post("/chat", json={"message": "what's today?"})
        self.assertEqual(reply.status_code, 200)
        sent = fake.calls[0]
        self.assertEqual(sent["cache_control"], {"type": "ephemeral"})
        self.assertIn("165 for 4 sets of 5", sent["system"][-1]["text"])
        self.assertEqual(json.loads(app.USAGE_FILE.read_text().splitlines()[0])["kind"], "chat")

    # ── Weekly program ────────────────────────────────────────────────────────
    def test_program_sees_last_weeks_training(self):
        # Week 2's program was a copy of week 1: on a Monday it saw no training at all.
        FakeDate.day = (2026, 9, 21)
        fake = FakeMessages(json.dumps({k: v for k, v in PROGRAM.items() if k != "week"}))
        app.client = SimpleNamespace(messages=fake)
        app.get_program(new=1)
        prompt = fake.calls[0]["messages"][0]["content"]
        self.assertIn("Mon 2026-09-14 (7 days ago)", prompt)
        self.assertIn("walking lunges 1 set (stopped — left knee ache)", prompt)
        self.assertIn("Today is Monday, 2026-09-21", prompt)

    # ── Cost trace ────────────────────────────────────────────────────────────
    def test_call_cost_prices_each_token_bucket(self):
        # 1000 in at $5/M + 100 out at $25/M + 500 cache writes at 1.25x + 2000 cache reads at 0.1x
        cost = coach.call_cost("claude-opus-5", usage(1000, 100, cache_write=500, cache_read=2000))
        self.assertAlmostEqual(cost, 0.005 + 0.0025 + 0.003125 + 0.001)

    def test_record_usage_never_raises(self):
        app.record_usage("chat", SimpleNamespace())   # no .usage at all
        app.record_usage("chat", SimpleNamespace(usage=usage(1000, 100)))
        row = json.loads(app.USAGE_FILE.read_text().splitlines()[-1])
        self.assertEqual((row["input_tokens"], row["cost_usd"]), (1000, 0.0075))


class LongHorizonTest(unittest.TestCase):
    """90 days of logs, built in code: what does the coach still see on day 90?
    The 10-day simulation never fills the memory windows (4 workouts vs a
    10-workout window), so this is where rolling over gets tested."""

    def setUp(self):
        self.build(90)

    def build(self, n_days: int) -> None:
        shutil.rmtree(DATA_DIR, ignore_errors=True)
        (DATA_DIR / "log").mkdir(parents=True)
        start = date(2026, 9, 14)
        days = [start + timedelta(days=i) for i in range(n_days)]
        FakeDate.day = (days[-1].year, days[-1].month, days[-1].day)
        workouts, weights, meals = ["# Training Log"], ["# Weight Log"], ["# Food Log"]
        for i, d in enumerate(days):
            week = i // 7
            if d.weekday() in (0, 4):
                workouts.append(f"- **{d}** — Upper: Bench {155 + 5 * week}x5x3; barbell row {125 + 5 * week}x8x3 (effort 7/10)")
            if d.weekday() == 2:
                extra = "; walking lunges 1 set (stopped — left knee ache)" if i == 2 else ""
                workouts.append(f"- **{d}** — Lower: Squat {185 + 5 * week}x5x3; RDL {155 + 5 * week}x8x3{extra} (effort 8/10)")
            weights.append(f"- **{d}** — {184.2 - 0.05 * i + ((i % 3) - 1) * 0.6:.1f} lb")
            meals += [f"- **{d} {t}** — dining hall meal: ~700 kcal, ~45g protein" for t in ("12:30", "19:00")]
            (DATA_DIR / "log" / f"{d}.md").write_text(f"\n### Session 18:30\n- **Judd:** day {i + 1} check-in\n- **Coach:** " + "Solid. " * 150)
        (DATA_DIR / "workouts.md").write_text("\n".join(workouts) + "\n")
        (DATA_DIR / "weight.md").write_text("\n".join(weights) + "\n")
        (DATA_DIR / "meals.md").write_text("\n".join(meals) + "\n")
        notes = "\n".join(f"- ({d}) Note {n}: a lasting fact the coach saved about his training or schedule."
                          for n, d in enumerate(days[::3]))
        (DATA_DIR / "profile.md").write_text(
            "# Judd's Profile\n\n## Injuries\n- Left knee patellar tendinitis: deep lunges and split squats flare it\n\n"
            f"## Notes the coach saved\n{notes}\n")

    def test_day_90_still_knows_the_start_and_the_knee(self):
        blocks = coach.build_system_prompt()
        today = blocks[-1]["text"]
        self.assertIn("patellar tendinitis", blocks[1]["text"])
        # Day 1's bench is long gone from the 10-workout window, but not from the lift history.
        training = today.split("## Recent training log\n")[1].split("\n## ")[0]
        self.assertEqual(len(training.splitlines()), 10)
        self.assertNotIn("2026-09-14", training)
        self.assertIn("Bench press: first 155x5 on Mon 2026-09-14 (89 days ago)", today)
        self.assertIn("Squat: first 185x5 on Wed 2026-09-16", today)
        self.assertIn("over 89 days (90 weigh-ins)", today)

    def test_prompt_stays_bounded(self):
        # Only the notes grow with time, about a line every 3 days. Everything else
        # is a fixed window or a one-line summary.
        self.build(30)
        day30 = sum(len(b["text"]) for b in coach.build_system_prompt())
        self.build(90)
        day90 = sum(len(b["text"]) for b in coach.build_system_prompt())
        print(f"\n  system prompt: day 30 {day30:,} chars, day 90 {day90:,} chars", file=sys.stderr)
        self.assertLess(day90, 30_000)


if __name__ == "__main__":
    unittest.main()
