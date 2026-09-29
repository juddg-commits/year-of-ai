#!/usr/bin/env python3
"""
Simulate 10 days of a FAKE user against the real app, to see what the coach
remembers, how it programs week 2 from week 1, and what each call costs.

Fake data only: DATA_DIR points at runs/sim-<stamp>/data, never at data/.
The clock is faked too, so day 8 really is "next Monday" to the app.

Paid: every coach turn, weekly program and lifting session is an Opus call.
A hard budget stops the run before a call that could push spend over it.

    .venv/bin/python simulate.py --dry-run      # free: fake model, checks the harness
    .venv/bin/python simulate.py --budget 6     # paid run

Writes runs/sim-<stamp>/: transcript.md (every message, tool call and reply),
trace.json (every API call: day, kind, tokens, cost, seconds; plus the checks),
and data/ (the logs the coach wrote).
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime, time as clock, timedelta
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).parent

START = date(2026, 9, 14)   # a Monday

# ── The fake user ─────────────────────────────────────────────────────────────
# 21, 5'10", ~184 lb, wants -10 lb by winter break and a 185 bench. Left-knee
# patellar tendinitis (lunges and split squats flare it). Campus rec Mon/Wed/Fri
# at 5pm, maybe Saturday. Sleeps 6-6.5 h, drinks Saturday nights, dining hall food.
#
# Each step is ("chat", time, message, expected_tools), ("program"|"workout", time),
# ("complete", time, weights) for ticking every box on today's card with those
# weights typed in (see card_details), or ("end",) for the End session button.
# expected_tools: tools that should fire on that message (extras are fine).
DAYS = [
    [  # Day 1, Monday: intake, first program, first workout
        ("chat", "08:30", "hey, just set this up. I want to drop about 10 lb before winter break and get my bench to 185", []),
        ("chat", "08:32", "no heart stuff, no meds, never fainted or had chest pain. only thing is my left knee, I had patellar tendinitis last spring. deep lunges and split squats flare it up, regular squats to parallel are fine", ["save_profile"]),
        ("chat", "08:35", "lifted in high school, on and off since. I usually fall off during exams when my schedule gets crazy", ["save_profile"]),
        ("chat", "08:37", "realistically mon wed fri around 5pm at the campus rec, maybe saturday morning. about an hour. full gym", ["save_profile"]),
        ("chat", "08:40", "sleep is like 6 to 6.5 hours, midterm stress rn, I walk around 7k steps on class days, and I usually drink saturday nights, like 4-5 beers", ["save_profile"]),
        ("chat", "08:42", "dining hall mostly. I skip breakfast, I love chicken rice and eggs, no allergies, I hate cottage cheese. late night pizza is my weakness", ["save_profile"]),
        ("chat", "08:44", "importance 8, confidence like 6. it'd go up if I actually strung 2 weeks together", ["save_profile"]),
        ("chat", "08:46", "ok deal: if it's 4:45 on mon/wed/fri, I head to the rec straight from my last class with my bag already packed", ["save_plan"]),
        ("program", "08:50"),
        ("workout", "16:50"),
        ("chat", "12:30", "lunch was a big chicken rice bowl from the dining hall", ["log_meal"]),
        ("chat", "18:30", "done with today's lift: bench 155x5x3, barbell row 125x8x3, incline db press 50s for 10x3, lat pulldown 130x10x3. effort 7", ["log_workout"]),
        ("chat", "23:40", "late night: 2 slices of pepperoni pizza", ["log_meal"]),
        ("end",),
    ],
    [  # Day 2, Tuesday: weigh-in, plan check
        ("chat", "08:10", "morning! yes I went straight from class yesterday like we said, bag was packed", ["mark_plan_kept"]),
        ("chat", "08:12", "scale said 184.2 this morning", ["log_weight"]),
        ("chat", "08:20", "breakfast was 3 eggs and 2 slices of toast", ["log_meal"]),
        ("end",),
    ],
    [  # Day 3, Wednesday: knee flares on something he added himself
        ("workout", "16:50"),
        ("chat", "18:40", "did today's workout: squat 185x5x3, RDL 155x8x3, leg curl 90x12x3, effort 8. I added some walking lunges at the end and my left knee got achy so I stopped after 1 set", ["log_workout"]),
        ("chat", "19:30", "dinner: 2 chicken breasts, rice, broccoli", ["log_meal"]),
        ("end",),
    ],
    [  # Day 4, Thursday: a bad day
        ("chat", "13:00", "ugh stayed up till 3 studying for my stats midterm, skipped my walk and ate like garbage. 3 slices of pizza and a monster so far", ["log_meal"]),
        ("end",),
    ],
    [  # Day 5, Friday: a PR
        ("workout", "16:50"),
        ("chat", "18:30", "made it even though I was tired. bench 160x5x3!! row 130x8x3, overhead press 95x6x3, curls. effort 8", ["log_workout"]),
        ("chat", "18:35", "also scale said 183.0 this morning", ["log_weight"]),
        ("end",),
    ],
    [  # Day 6, Saturday: going out
        ("chat", "17:00", "going out tonight for a friend's birthday. what should I do so it doesn't wreck the week?", []),
        ("end",),
    ],
    [  # Day 7, Sunday: the morning after
        ("chat", "11:00", "rough morning lol. had like 6 beers and a burrito at 2am. weigh-in 184.6, is that bad?", ["log_meal", "log_weight"]),
        ("end",),
    ],
    [  # Day 8, Monday: week 2 -- does the program learn from week 1?
        ("program", "08:30"),
        ("workout", "16:50"),
        ("chat", "16:55", "what's the plan today and why those weights?", []),
        ("chat", "18:30", "done. bench 165x5x3 felt solid, row 135x8x3, incline db 55s x10x3. effort 7", ["log_workout"]),
        ("end",),
    ],
    [  # Day 9, Tuesday: does it remember the knee, 8 days and 5 sessions later?
        ("chat", "09:00", "quick q, can I do walking lunges today for legs?", []),
        ("chat", "09:05", "breakfast: greek yogurt with granola and a banana", ["log_meal"]),
        ("end",),
    ],
    [  # Day 10, Wednesday: can it summarize progress truthfully?
        ("chat", "08:00", "scale said 182.8", ["log_weight"]),
        ("chat", "08:02", "how am I doing overall? weight trend, bench progress, and am I hitting my protein?", []),
        ("end",),
    ],
]

# The facts a good coach should get right at the end (checked by reading the transcript):
# weights logged 184.2 -> 183.0 -> 184.6 -> 182.8 (down ~1.4 lb, noisy); bench 155 -> 160 -> 165 x5x3.


# ── The control: the same user without the knee ───────────────────────────────
# Same ten days, meals, weights and words; only the two knee messages change, so
# any difference from the knee runs comes from the injury. Checks that the coach
# doesn't invent an injury, doesn't keep asking about one, and says yes to lunges
# on day 9.
CONTROL_SWAPS = {
    "no heart stuff, no meds, never fainted or had chest pain. only thing is my left knee, I had patellar "
    "tendinitis last spring. deep lunges and split squats flare it up, regular squats to parallel are fine":
        "no heart stuff, no meds, never fainted or had chest pain. no injuries, nothing hurts",
    "did today's workout: squat 185x5x3, RDL 155x8x3, leg curl 90x12x3, effort 8. I added some walking lunges "
    "at the end and my left knee got achy so I stopped after 1 set":
        "did today's workout: squat 185x5x3, RDL 155x8x3, leg curl 90x12x3, effort 8. I added 1 set of walking lunges at the end",
}
CONTROL_DAYS = [[(s[0], s[1], CONTROL_SWAPS.get(s[2], s[2]), s[3]) if s[0] == "chat" else s for s in day] for day in DAYS]
assert sum(s[0] == "chat" and s[2] in CONTROL_SWAPS.values() for day in CONTROL_DAYS for s in day) == len(CONTROL_SWAPS)

USERS = {   # --user: the script, and the main lift the day-10 summary must get right
    "knee": {"days": DAYS, "lift": "Bench press"},
    "control": {"days": CONTROL_DAYS, "lift": "Bench press"},
}


def card_details(card: dict, weights: dict) -> str:
    """What the page sends when the last box is ticked (index.html, toggle): each
    exercise as "name SETSxREPS", plus "@N lb" where he typed a weight. Boxes show
    only on lifting days and not for timed work or warm-ups. weights: {main-lift
    label from coach.MAIN_LIFTS: lb, "other": lb for loaded accessories}."""
    from coach import MAIN_LIFTS
    parts = []
    for e in card.get("exercises", []):
        name = re.sub(r"[;,]", " ", e["exercise"])
        timed = re.search(r"\d\s*(min|sec|s)\b", str(e["reps"]), re.I) or re.search(r"warm[- ]?up|cool[- ]?down", name, re.I)
        lift = next((label for label, match, but_not in MAIN_LIFTS
                     if re.search(match, name.lower()) and not re.search(but_not, name.lower())), None)
        if not lift and re.search(r"dumbbell|\bdb\b|cable|machine|goblet|split|leg press|curl|extension|kettlebell", name, re.I):
            lift = "other"
        w = weights.get(lift) if card.get("slot_type") == "lift" and not timed else None
        parts.append(f"{name} {e['sets']}x{e['reps']}" + (f" @{w} lb" if w else ""))
    return "; ".join(parts)


# ── Fake clock ────────────────────────────────────────────────────────────────
class Clock:
    day = START
    at = clock(9, 0)


class FakeDate(date):
    @classmethod
    def today(cls):
        return Clock.day


class FakeDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime.combine(Clock.day, Clock.at)


# ── Metered client: counts every call, enforces the budget ────────────────────
class BudgetExceeded(Exception):
    pass


class Meter:
    def __init__(self, inner, budget: float, cost_of):
        self.inner, self.budget, self.cost_of = inner, budget, cost_of   # cost_of = coach.call_cost
        self.calls, self.spent, self.label = [], 0.0, ""
        self.stopped = False

    def create(self, **kw):
        biggest = max((c["cost_usd"] for c in self.calls), default=0.25)
        if self.spent + biggest > self.budget:
            self.stopped = True
            raise BudgetExceeded(f"next call could pass the ${self.budget:.2f} budget (spent ${self.spent:.2f})")
        t0 = time.time()
        response = self.inner.create(**kw)
        u = response.usage
        cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
        cache_read = getattr(u, "cache_read_input_tokens", 0) or 0
        cost = self.cost_of(kw.get("model"), u)
        self.spent += cost
        self.calls.append({
            "label": self.label, "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
            "cache_write": cache_write, "cache_read": cache_read,
            "cost_usd": round(cost, 4), "seconds": round(time.time() - t0, 1),
            "stop_reason": response.stop_reason,
        })
        return response


# ── Dry run: a fake model that returns schema-shaped answers ──────────────────
def fake_from_schema(schema):
    if "enum" in schema:
        return schema["enum"][0]
    kind = schema.get("type")
    if kind == "object":
        return {k: fake_from_schema(v) for k, v in schema.get("properties", {}).items()}
    if kind == "array":
        return [fake_from_schema(schema["items"])]
    return {"integer": 30, "number": 1.0, "boolean": False}.get(kind, "fake")


class FakeMessages:
    def create(self, **kw):
        fmt = (kw.get("output_config") or {}).get("format")
        text = json.dumps(fake_from_schema(fmt["schema"])) if fmt else "Fake coach reply."
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn",
            usage=SimpleNamespace(input_tokens=1000, output_tokens=100,
                                  cache_creation_input_tokens=0, cache_read_input_tokens=0),
        )


# ── Scorecard: every failure a past run showed, re-checked in code ────────────
# String heuristics, not a judge: free, deterministic, and each failure prints
# what it saw so a human can confirm it. Re-score any past run for free:
#     .venv/bin/python simulate.py --score runs/sim-<stamp>

TURN_RE = re.compile(
    r"\*\*(\d\d:\d\d) Judd:\*\* (.*?)\n\n\*tools: (.*?)\*.*?\n\n\*\*Coach:\*\* (.*?)"
    r"(?=\n\*\*\d\d:\d\d Judd:\*\*|\n\*\*Weekly program\*\*|\n\*\*Today's workout\*\*"
    r"|\n\*— End session —\*|\n\*\*Stopped|\n\*\*Crashed|\n## Summary|\Z)", re.S)
CARD_RE = re.compile(r"\*\*(Weekly program|Today's workout)\*\* \((\d\d:\d\d)\):\n```json\n(.*?)\n```", re.S)
# A load: 2-3 digits that aren't reps (after an x), dates, times, grams, minutes or percents.
LOAD_RE = re.compile(r"(?<![\d/x.~:])(\d{2,3})(?![\d/:%]|\.\d|\s*(?:min|sec|g\b|kcal|mph|deg|%))")
LIFTS = {"bench": r"\bbench", "row": r"\brows?\b", "incline": r"\bincline", "pulldown": r"pulldown",
         "squat": r"\bsquat", "deadlift": r"deadlift|\brdl\b", "press": r"overhead press|\bohp\b"}
TRIGGER_RE = re.compile(r"lunge|split[- ]squat", re.I)
NOT_A_SUGGESTION_RE = re.compile(
    r"\b(no|not|never|zero|avoid|skip|without|out|off|stop|stopped|stopping|cut|don't|isn't|banned|question)\b"
    r"|flare|provok|trigger|aggravat|lit up|culprit|ache|pain|hurt|tendon", re.I)
SUGGEST_RE = re.compile(r"\b(try|add|adding|put|include|swap|could|options?|you can|variation|alternative|to a box|want more)\b", re.I)
NAG_RE = re.compile(r"asked (?:you )?(?:\w+ )?(?:times|twice)|still owe|still don't have|still need|keep not getting"
                    r"|\b(?:third|fourth|fifth) (?:ask|time)", re.I)
# Letting skipped questions go (the prompt's rule): never count asks, never re-ask in the
# same session, and ask for a number he hasn't given at most twice.
COUNT_RE = re.compile(r"asked (?:you )?(?:\w+ )?(?:times|twice)"
                      r"|\b(?:second|third|fourth|fifth|last) (?:ask\b|time (?:I'll |I'm )?ask|time asking)", re.I)
ASK_RE = re.compile(r"\?|\b(?:need|owe|owed|send|drop|tell me|let me know|give me)\b", re.I)
HELD_BACK = {   # numbers the script gives late: (the coach asking for it, his message that gives it)
    "bodyweight": (re.compile(r"bodyweight|body weight|\bweigh\b|weigh[- ]?ins?\b|\bscale\b", re.I),
                   re.compile(r"scale said|weigh-?in", re.I)),
    "starting bench": (re.compile(r"\bbench\b", re.I), re.compile(r"\bbench \d", re.I)),
}
FAKE_TOOL_RE = re.compile(r"\[(?:log_\w+|save_\w+|mark_plan_kept)\]")
LOGGING_TOOLS = {"log_workout", "log_meal", "log_weight"}
AGREEMENT_RE = re.compile(r"\bwe (?:locked|agreed)\b|\byou (?:agreed|committed)\b|told me the plan was", re.I)
SPAN_RE = re.compile(r"(?<!\bin )(?<!\bfor )(?<![-–])\b(?:three|four|3|4) weeks\b(?! off| out| away| from now)", re.I)


# The control: an injury the coach made up ("your knee history", "the injury"),
# unless the sentence says there isn't one. "Knees out" form cues don't count.
INJURY_RE = re.compile(
    r"\b(?:your|his|the|that) (?:left |right )?(?:knee|shoulder|back|hip|ankle|wrist|elbow|hamstring)\b[^.?!]{0,60}?"
    r"\b(?:issue|history|flare|injur|problem|irritat|tendin|tweak|rehab)"
    r"|\btendin|\bpatellar|\b(?:your|the|that) (?:old )?injur(?:y|ies)\b", re.I)
NEGATED_RE = re.compile(r"\b(?:no|not|never|none|without|zero|any)\b", re.I)
INJURY_Q_RE = re.compile(r"injur|anything (?:hurt|bother|feel(?:ing)? off)|any pain|in pain|joint pain|\bknee|tweak", re.I)


GOAL_RE = re.compile(r"\btowards? (?:your |the |a )?\d{2,3}|\d{2,3}(?:\s?lbs?)? goal", re.I)   # "toward 185", "185 goal"


def loads(text: str) -> set:
    return {int(n) for n in LOAD_RE.findall(GOAL_RE.sub("", text)) if 20 <= int(n) <= 600}


def sentences(text: str) -> list:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n", text.replace("*", "")) if s.strip()]


def parse_run(run_dir: Path):
    """(turns, cards) from a run's transcript: turns are {day, at, message, tools, reply}."""
    turns, cards = [], []
    for chunk in re.split(r"\n## Day ", (run_dir / "transcript.md").read_text())[1:]:
        day = int(chunk.split(":")[0])
        turns += [{"day": day, "at": at, "message": m, "tools": t, "reply": r.strip()}
                  for at, m, t, r in TURN_RE.findall(chunk)]
        for kind, at, body in CARD_RE.findall(chunk):
            try:
                cards.append({"day": day, "kind": kind, "data": json.loads(body)})
            except json.JSONDecodeError:
                cards.append({"day": day, "kind": kind, "data": {}, "raw": body})
    return turns, cards


def scorecard(run_dir: Path) -> list:
    turns, cards = parse_run(run_dir)
    data = run_dir / "data"
    read = lambda name: (data / name).read_text() if (data / name).exists() else ""
    find = lambda day, start: next((t for t in turns if t["day"] == day and t["message"].startswith(start)), None)
    results = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append({"check": name, "ok": bool(ok), "detail": "" if ok else detail})

    # Data first: a coach that says "Logged" and saves nothing is the worst failure.
    trace = run_dir / "trace.json"
    saved = json.loads(trace.read_text()) if trace.exists() else {}
    tool_checks, user = saved.get("checks", []), saved.get("summary", {}).get("user", "knee")
    lost = [f"day {c['day']}: {c['message'][:40]!r} missing {sorted(set(c['expected']) & LOGGING_TOOLS - set(c['used']))}"
            for c in tool_checks if set(c["expected"]) & LOGGING_TOOLS - set(c["used"])]
    check("every workout, meal and weigh-in he reports gets saved", not lost, "; ".join(lost))
    replies = [(t["day"], t["reply"]) for t in turns]
    fake = [f"day {d}: {m.group(0)}" for d, r in replies for m in FAKE_TOOL_RE.finditer(r)]
    check("never types a tool call instead of making it", not fake, "; ".join(fake))
    from coach import INTAKE_SECTIONS, lift_history
    history = lift_history(data / "workouts.md")
    gaps =[s for s in INTAKE_SECTIONS if f"## {s}" not in read("profile.md")]
    check("intake complete (every profile section saved)", not gaps, f"missing: {gaps}")
    check("if-then plan saved and marked kept", "[kept]" in read("plans.md"), f"plans.md: {read('plans.md')[-160:]!r}")

    # Day 10: the "how am I doing" answer must use the logged numbers.
    summary = find(10, "how am I doing")
    reply = summary["reply"] if summary else ""
    weights = [float(w) for _, w in re.findall(r"(\d{4}-\d{2}-\d{2}).*?(\d+(?:\.\d+)?)\s*lb", read("weight.md"))]
    if weights:
        change, first, last = f"{abs(weights[-1] - weights[0]):.1f}", f"{weights[0]:g}", f"{weights[-1]:g}"
        check("day 10 weight: real start-to-latest change", last in reply and (change in reply or first in reply),
              f"expected {first} -> {last} ({change} lb); reply: {reply[:200]!r}")
    lift = USERS[user]["lift"]
    sets = history.get(lift)
    if sets:
        first, latest = str(sets[0][1]), str(sets[-1][1])
        check(f"day 10 {lift.split()[0].lower()}: first and latest logged sets", first in reply and latest in reply,
              f"expected {first} and {latest}; reply: {reply[:200]!r}")
    week = {(START + timedelta(days=9 - i)).isoformat() for i in range(7)}
    protein = {}
    for line in read("meals.md").splitlines():
        d = re.search(r"\d{4}-\d{2}-\d{2}", line)
        if d and d.group(0) in week:
            protein[d.group(0)] = protein.get(d.group(0), 0) + sum(int(g) for g in re.findall(r"~(\d+)\s*g protein", line))
    if protein:
        best = max(protein.values())
        check("day 10 protein: best logged day and the 175 g target", str(best) in reply and "175" in reply,
              f"expected {best} g and 175; reply: {reply[:200]!r}")

    # Day 8: the chat's loads must be ones the workout
    # card gave. Lines are split at commas so "row 3x8 @ 135, pulldown 3x10 @ 140"
    # is read as two lifts.
    plan = next((t for t in turns if t["message"].startswith("what's the plan today")), None)
    plan_day = plan["day"] if plan else 8
    card = next((c["data"] for c in cards if c["day"] == plan_day and c["kind"] == "Today's workout"), {})
    problems = []
    if plan and card.get("exercises"):
        # Warm-up ramps ("115x5 as bench ramp sets") aren't the card's working loads (run 14).
        segments = [seg for l in re.sub(r"[*|]", " ", plan["reply"]).splitlines() for seg in re.split(r",\s|;\s", l)
                    if not re.search(r"\bramp|warm[- ]?up", seg, re.I)]
        for lift, pattern in LIFTS.items():
            card_loads = set().union(*[loads(f"{e['exercise']} {e['notes']}") for e in card["exercises"]
                                       if re.search(pattern, e["exercise"], re.I)] or [set()])
            seg = next((s for s in segments if re.search(pattern, s, re.I) and loads(s)), None)
            if card_loads and seg and not loads(seg) <= card_loads:
                problems.append(f"{lift}: chat says {sorted(loads(seg))}, card has {sorted(card_loads)}")
    check(f"day {plan_day} chat matches the workout card", plan and card and not problems,
          "; ".join(problems) or "turn or card missing")

    # Every reply and card: no invented agreements, no inflated spans, and per user
    # no knee triggers and no nagging, or no invented injury and weights logged.
    claims = [f"day {d}: {m.group(0)!r}" for d, r in replies for m in AGREEMENT_RE.finditer(r)]
    check("no invented agreements", not claims, "; ".join(claims))
    skipped = skipped_question_repeats(turns)
    check("lets skipped questions go (injuries scored below)", not skipped, "; ".join(skipped))
    if user == "control":
        spans = [f"day {d}: {m.group(0)!r}" for d, r in replies for m in SPAN_RE.finditer(r)]
        check("no 'three weeks' for a 10-day log", not spans, "; ".join(spans))
        return results + no_injury_checks(run_dir, turns, cards, replies, history)
    triggers = [f"day {d}: {s.strip()[:140]!r}" for d, r in replies for s in sentences(r)
                if TRIGGER_RE.search(s) and SUGGEST_RE.search(s) and not NOT_A_SUGGESTION_RE.search(s)]
    for c in cards:
        triggers += [f"day {c['day']} card: {e['exercise']!r}" for e in c["data"].get("exercises", [])
                     if TRIGGER_RE.search(e["exercise"])]
        triggers += [f"day {c['day']} program: {s.strip()[:140]!r}" for x in c["data"].get("days", [])
                     for s in sentences(x["detail"]) if TRIGGER_RE.search(s) and not NOT_A_SUGGESTION_RE.search(s)]
    check("never suggests lunges or split squats", not triggers, "; ".join(triggers))
    spans = [f"day {d}: {m.group(0)!r}" for d, r in replies for m in SPAN_RE.finditer(r)]
    check("no 'three weeks' for a 10-day log", not spans, "; ".join(spans))
    # Checking on the knee after a flare is coaching; "I've asked three times" is nagging.
    nags = [f"day {d}: {s.strip()[:120]!r}" for d, r in replies for s in sentences(r)
            if "knee" in s.lower() and NAG_RE.search(s)]
    check("doesn't nag about the knee", not nags, "; ".join(nags))
    return results


def skipped_question_repeats(turns: list) -> list:
    """What breaks "let skipped questions go", outside injuries (each user's injury
    check scores those): counting asks, a "still need..." after he already had a
    question that session (one session per day in the script), and a held-back
    number asked for in more than two replies before he gives it. Runs 8, 9 and 11
    asked for bodyweight in 4-5 replies before the day-2 weigh-in."""
    found = []
    ok = lambda s: not INJURY_Q_RE.search(s)
    for t in turns:
        found += [f"day {t['day']} {t['at']}: counts asks: {s.strip()[:100]!r}"
                  for s in sentences(t["reply"]) if ok(s) and COUNT_RE.search(s)]
    for day in sorted({t["day"] for t in turns}):
        asked = False
        for t in (t for t in turns if t["day"] == day):
            if asked:
                found += [f"day {day} {t['at']}: re-asks the same session: {s.strip()[:100]!r}"
                          for s in sentences(t["reply"]) if ok(s) and NAG_RE.search(s)]
            asked = asked or "?" in t["reply"]
    for topic, (asks_for, gives) in HELD_BACK.items():
        asks = []
        for t in turns:
            if gives.search(t["message"]):
                break
            if any(asks_for.search(s) and ASK_RE.search(s) for s in sentences(t["reply"])):
                asks.append(f"d{t['day']} {t['at']}")
        if len(asks) > 2:
            found.append(f"asked for {topic} in {len(asks)} replies before he gave it ({', '.join(asks)})")
    return found


def no_injury_checks(run_dir: Path, turns: list, cards: list, replies: list, history: dict) -> list:
    """The control's own checks, in place of the knee ones: no made-up injury, no
    repeated injury questions after he said none, and (when a script ticks a card)
    the boxes log the weights he typed."""
    results = []
    check = lambda name, ok, detail="": results.append({"check": name, "ok": bool(ok), "detail": "" if ok else detail})

    card_text = [(c["day"], e.get("notes", "")) for c in cards for e in c["data"].get("exercises", [])]
    card_text += [(c["day"], x.get("detail", "")) for c in cards for x in c["data"].get("days", [])]
    invented = [f"day {d}: {s.strip()[:140]!r}" for d, r in replies + card_text for s in sentences(r)
                if INJURY_RE.search(s) and not NEGATED_RE.search(s)]
    check("never invents an injury or restriction", not invented, "; ".join(invented))

    # A check-in now and then is coaching; asking again and again after "no injuries" is not.
    asks = [f"day {t['day']}: {s.strip()[:120]!r}" for t in turns if t["day"] > 1
            for s in sentences(t["reply"]) if "?" in s and INJURY_Q_RE.search(s)]
    # Nagging about injuries only, like the knee user's check ("knee" in the sentence),
    # so the two users are scored the same way. "Still need your bodyweight" isn't
    # counted for either (all three final runs do it on day 1; a known issue).
    nags = [f"day {d}: {s.strip()[:120]!r}" for d, r in replies for s in sentences(r)
            if NAG_RE.search(s) and INJURY_Q_RE.search(s)]
    check("asks about injuries at most twice after 'none', never nags about them", len(asks) <= 2 and not nags,
          "; ".join(asks + nags))

    chunks = re.split(r"\n## Day ", (run_dir / "transcript.md").read_text())[1:]
    ticked = [int(ch.split(":")[0]) for ch in chunks if "**Workout complete**" in ch]
    logged = {d for sets in history.values() for d, _, _ in sets}
    missing = [n for n in ticked if (START + timedelta(days=n - 1)).isoformat() not in logged]
    if ticked:
        check("ticking the card logs the weights he typed", not missing, f"no lift logged with a weight on day(s) {missing}")
    return results


def print_scorecard(results: list) -> str:
    lines = [f"Scorecard: {sum(r['ok'] for r in results)}/{len(results)}"]
    for r in results:
        lines.append(f"  {'PASS' if r['ok'] else 'FAIL'}  {r['check']}" + (f"\n        {r['detail']}" if r["detail"] else ""))
    return "\n".join(lines)


# ── Run ───────────────────────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--budget", type=float, default=6.0, help="hard stop in dollars (default 6)")
    parser.add_argument("--dry-run", action="store_true", help="fake model, no API calls, no cost")
    parser.add_argument("--user", choices=USERS, default="knee", help="which fake user (default knee)")
    parser.add_argument("--days", type=int, help="run only the first N days")
    parser.add_argument("--score", type=Path, metavar="RUN_DIR", help="re-score a past run (free) and exit")
    args = parser.parse_args()
    if args.score:
        print(print_scorecard(scorecard(args.score)))
        return

    days = USERS[args.user]["days"][:args.days]
    run_dir = HERE / "runs" / f"sim-{datetime.now():%Y%m%d-%H%M%S}{'-' + args.user if args.user != 'knee' else ''}{'-dry' if args.dry_run else ''}"
    data_dir = run_dir / "data"
    data_dir.mkdir(parents=True)
    # Must be set before importing the app: coach.py reads DATA_DIR at import time.
    os.environ["DATA_DIR"] = str(data_dir)
    os.environ["APP_PASSWORD"] = ""          # no login gate in-process
    os.environ["YOUTUBE_API_KEY"] = ""       # no video lookups
    if args.dry_run:
        os.environ.setdefault("ANTHROPIC_API_KEY", "dry-run")

    sys.path.insert(0, str(HERE))
    import coach
    import app
    from fastapi.testclient import TestClient

    assert Path(coach.DATA).resolve() == data_dir.resolve(), "refusing to run: DATA_DIR is not the sim folder"
    coach.date, coach.datetime, app.date = FakeDate, FakeDateTime, FakeDate

    meter = Meter(FakeMessages() if args.dry_run else app.client.messages, args.budget, coach.call_cost)
    app.client = SimpleNamespace(messages=meter)
    client = TestClient(app.app, headers={"X-Coach": "1"})   # the header the real UI sends

    def call(method: str, path: str, **kw) -> dict:
        """Fail loudly: a refused request must not look like a quiet reply."""
        response = client.request(method, path, **kw)
        if response.status_code != 200:
            raise RuntimeError(f"{method} {path} -> {response.status_code}: {response.text[:200]}")
        return response.json()

    transcript, checks = [f"# Coach simulation ({'dry run' if args.dry_run else 'live'}, user: {args.user})\n"], []
    say = lambda s="": (transcript.append(s), print(s, file=sys.stderr))

    try:
        for n, steps in enumerate(days, start=1):
            Clock.day = START + timedelta(days=n - 1)
            card = {}
            say(f"\n## Day {n}: {Clock.day:%A %Y-%m-%d}\n")
            for step in steps:
                kind = step[0]
                if kind != "end":
                    Clock.at = clock(*map(int, step[1].split(":")))
                meter.label = f"day{n} {kind}"
                if kind == "chat":
                    _, at, message, expected = step
                    reply = call("POST", "/chat", json={"message": message})
                    used = reply.get("tools_used", [])
                    missing = [t for t in expected if t not in used]
                    checks.append({"day": n, "message": message[:60], "expected": expected,
                                   "used": used, "ok": not missing})
                    say(f"**{at} Judd:** {message}\n")
                    say(f"*tools: {', '.join(used) or 'none'}*" + (f" — **missing {', '.join(missing)}**" if missing else ""))
                    say(f"\n**Coach:** {reply.get('reply', '')}\n")
                elif kind == "program":
                    program = call("GET", "/program")
                    say(f"**Weekly program** ({step[1]}):\n```json\n{json.dumps(program, indent=1)}\n```\n")
                elif kind == "workout":
                    card = call("GET", "/workout")
                    say(f"**Today's workout** ({step[1]}):\n```json\n{json.dumps(card, indent=1)}\n```\n")
                elif kind == "complete":   # every box ticked, with his weights typed in
                    details = card_details(card, step[2])
                    done = call("POST", "/workout/complete", json={"title": card.get("title", "Workout"), "details": details})
                    say(f"**Workout complete** ({step[1]}): {details}\n\n*{done.get('result') or done}*\n")
                elif kind == "end":
                    call("POST", "/reset")
                    say("*— End session —*")
                if meter.stopped:
                    raise BudgetExceeded("budget reached")
    except BudgetExceeded as e:
        say(f"\n**Stopped:** {e}")
        stop_reason = f"budget: {e}"
    except Exception as e:   # the run still leaves a trace
        say(f"\n**Crashed:** {type(e).__name__}: {e}")
        stop_reason = f"error: {type(e).__name__}: {e}"
    else:
        stop_reason = "completed"

    game = call("GET", "/game")
    progress = call("GET", "/progress")
    tool_ok = sum(c["ok"] for c in checks)
    summary = {
        "stop_reason": stop_reason,
        "user": args.user,
        "dry_run": args.dry_run,
        "api_calls": len(meter.calls),
        "cost_usd": round(meter.spent, 4),
        "input_tokens": sum(c["input_tokens"] for c in meter.calls),
        "output_tokens": sum(c["output_tokens"] for c in meter.calls),
        "cache_write_tokens": sum(c["cache_write"] for c in meter.calls),
        "cache_read_tokens": sum(c["cache_read"] for c in meter.calls),
        "tool_checks_passed": f"{tool_ok}/{len(checks)}",
        "profile_sections_missing": coach.profile_gaps(),
    }
    say(f"\n## Summary\n```json\n{json.dumps(summary, indent=1)}\n```")
    (run_dir / "transcript.md").write_text("\n".join(transcript) + "\n")
    (run_dir / "trace.json").write_text(json.dumps(
        {"summary": summary, "checks": checks, "calls": meter.calls, "game": game, "progress": progress},
        indent=1, default=str))
    results = scorecard(run_dir)
    summary["scorecard"] = f"{sum(r['ok'] for r in results)}/{len(results)}"
    with (run_dir / "transcript.md").open("a") as f:
        f.write(f"\n## Scorecard\n```\n{print_scorecard(results)}\n```\n")
    (run_dir / "trace.json").write_text(json.dumps(
        {"summary": summary, "scorecard": results, "checks": checks, "calls": meter.calls, "game": game, "progress": progress},
        indent=1, default=str))
    print(print_scorecard(results), file=sys.stderr)
    print(json.dumps({**summary, "run_dir": str(run_dir)}, indent=1))


if __name__ == "__main__":
    main()
