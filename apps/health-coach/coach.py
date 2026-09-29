#!/usr/bin/env python3
"""
Health Coach v2 — now with TOOL USE (the core agent pattern).

v1 could only talk. v2 can DO: the model decides mid-conversation to call
functions in this file — logging workouts, saving notes to its own memory.
That decide→call→result→continue loop is the heart of every AI agent you'll
build this year. Read `get_coach_reply()` — that's the whole lesson.

Run:  .venv/bin/python coach.py       (type 'quit' to end and save)
"""

import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import anthropic

# --- Config ------------------------------------------------------------------
MODEL = "claude-opus-5"
MAX_TOKENS = 4096  # caps thinking + reply together, so leave headroom
PROTEIN_TARGET_G = 175

# $ per million tokens (input, output). Cache writes (5-minute TTL) cost 1.25x
# input, cache reads 0.1x. Used for the cost trace and the simulation budget.
PRICES = {"claude-opus-5": (5.00, 25.00)}
CACHE_WRITE_MULT, CACHE_READ_MULT = 1.25, 0.10

HERE = Path(__file__).parent
DATA = Path(os.environ.get("DATA_DIR") or HERE / "data")   # deploys point this at a persistent volume
PROFILE = DATA / "profile.md"
WORKOUTS = DATA / "workouts.md"
MEALS = DATA / "meals.md"
WEIGHTS = DATA / "weight.md"   # the log_weight tool writes it; the Progress chart reads it
PLANS = DATA / "plans.md"   # written by the save_plan tool; read by the coach + Journey tab
LOG_DIR = DATA / "log"
RECENT_LOGS_TO_LOAD = 3
NOTES_SECTION = "Notes the coach saved"
FOOD_DAYS_TO_LOAD = 7

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
WEIGHT_RE = re.compile(r"(\d{4}-\d{2}-\d{2}).*?(\d+(?:\.\d+)?)\s*lb")
PROTEIN_RE = re.compile(r"~(\d+)\s*g protein")
KCAL_RE = re.compile(r"~(\d+)\s*kcal")


def call_cost(model: str, usage) -> float:
    """Dollars for one API call. input_tokens excludes cached tokens, so the
    three input buckets add up without double counting."""
    price_in, price_out = PRICES.get(model, PRICES["claude-opus-5"])
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    return (usage.input_tokens * price_in + cache_write * price_in * CACHE_WRITE_MULT
            + cache_read * price_in * CACHE_READ_MULT + usage.output_tokens * price_out) / 1e6


# --- Tiny .env loader ---------------------------------------------------------
def load_dotenv() -> None:
    env = HERE / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# --- INTAKE: the structured consultation every real coach runs -----------------
# profile.md is organized into these sections. Any that are missing, the coach
# covers in conversation — ONE topic at a time — and saves via save_profile.
INTAKE_SECTIONS = [
    "Safety",                 # PAR-Q style screen — always first if missing
    "Goals",                  # goal + the why + timeline
    "Training History",       # experience, past attempts, why they failed
    "Injuries",               # current/past, what aggravates them
    "Schedule & Equipment",   # TRUE weekly availability, gym access, equipment
    "Lifestyle",              # sleep, stress, job activity, steps, alcohol/social
    "Nutrition Habits",       # likes/dislikes/allergies, eating pattern
    "Readiness",              # importance 1-10, confidence 1-10 (MI rulers)
]


def profile_gaps() -> list:
    """Which intake sections haven't been completed yet."""
    text = PROFILE.read_text() if PROFILE.exists() else ""
    return [s for s in INTAKE_SECTIONS if f"## {s}" not in text]


def intake_status() -> str:
    """Told to the model after every profile write. It used to say only "cover
    ONE next time", so the coach kept asking about a knee it already knew about."""
    remaining = profile_gaps()
    if not remaining:
        return "Intake complete — the full consultation is on file."
    return (
        f"Intake sections still open: {', '.join(remaining)}. If the profile or notes "
        "already cover one, save that section now with save_profile instead of asking "
        "him again. Otherwise cover ONE of them next time it fits naturally."
    )


def upsert_section(text: str, section: str, content: str) -> str:
    """Replace a '## Section' block, or append it — never clobbers the rest."""
    block = f"## {section}\n{content.strip()}\n"
    pattern = re.compile(rf"## {re.escape(section)}\n.*?(?=\n## |\Z)", re.S)
    if pattern.search(text):
        return pattern.sub(lambda _: block, text)   # a function: backslashes in model text stay literal
    return (text.rstrip() + "\n\n" if text.strip() else "# Judd's Profile\n\n") + block


# --- TOOLS: what the model is allowed to DO ------------------------------------
# Each tool = a name, a description (the model reads this to decide WHEN to call
# it — descriptions are prompts!), and a JSON schema for its inputs.
TOOLS = [
    {
        "name": "log_workout",
        "description": (
            "Save a workout Judd completed to his training log. Use this whenever "
            "he mentions finishing any exercise. If key details are missing, ask "
            "him first, then log."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "activity": {"type": "string", "description": "e.g. 'push day', '5k run'"},
                "details": {"type": "string", "description": "sets/reps/weights, or distance/time"},
                "effort_1_to_10": {"type": "integer", "description": "how hard it felt, 1-10"},
            },
            "required": ["activity", "details"],
        },
    },
    {
        "name": "save_note",
        "description": (
            "Save a lasting fact about Judd to his profile so future sessions "
            "remember it — goals, injuries, preferences, schedule changes. "
            "Use it when he shares something worth remembering long-term."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"note": {"type": "string", "description": "one clear sentence"}},
            "required": ["note"],
        },
    },
    {
        "name": "log_meal",
        "description": (
            "Log food Judd ate. Whenever he mentions eating or drinking something, "
            "YOU estimate the calories and protein from his description (be "
            "reasonable, round numbers are fine) and log it. If the description "
            "is too vague to estimate (e.g. 'had lunch'), ask what it was first."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {"type": "string", "description": "what he ate, e.g. '2 eggs + toast w/ butter'"},
                "estimated_calories": {"type": "integer", "description": "your estimate, kcal"},
                "estimated_protein_g": {"type": "integer", "description": "your estimate, grams"},
            },
            "required": ["description", "estimated_calories"],
        },
    },
    {
        "name": "save_profile",
        "description": (
            "Save ONE completed intake section to Judd's profile after covering "
            "that topic in conversation. Consolidate anything already known from "
            "notes or context into the section — never re-ask answered questions. "
            "Write concise markdown bullets a coach would actually use."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "enum": [
                        "Safety", "Goals", "Training History", "Injuries",
                        "Schedule & Equipment", "Lifestyle", "Nutrition Habits", "Readiness",
                    ],
                },
                "content": {"type": "string", "description": "the section content, markdown bullets"},
            },
            "required": ["section", "content"],
        },
    },
    {
        "name": "mark_plan_kept",
        "description": (
            "When Judd confirms he FOLLOWED THROUGH on his most recent if-then "
            "plan, mark it kept. Only on his clear confirmation — never assume."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "log_weight",
        "description": (
            "Log Judd's bodyweight whenever he reports a weigh-in or states his "
            "current weight (e.g. 'I weigh 185', 'scale said 183.4 this morning'). "
            "One entry per report; the Progress chart is built from these."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pounds": {"type": "number", "description": "bodyweight in pounds, as he reported it (decimals fine)"},
            },
            "required": ["pounds"],
        },
    },
    {
        "name": "save_plan",
        "description": (
            "Save the ONE if-then plan agreed at the end of a session. Call it once "
            "per session, only after Judd has agreed to the plan in his own words. "
            "The next session opens by checking this plan."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "if_trigger": {"type": "string", "description": "the cue: time + context, e.g. 'tomorrow at 7am, right after coffee'"},
                "then_action": {"type": "string", "description": "the specific action, e.g. '20-minute walk before opening the laptop'"},
            },
            "required": ["if_trigger", "then_action"],
        },
    },
]


# "[log_meal] " markers that older session logs wrote into the coach's lines.
TOOL_MARKER_RE = re.compile(r"\[(?:%s)\] ?" % "|".join(t["name"] for t in TOOLS))

def execute_tool(name: str, tool_input: dict) -> str:
    """Actually run the function the model asked for. Returns a string the
    model will read as the result — tell it what happened."""
    today = date.today().isoformat()

    if name == "log_workout":
        DATA.mkdir(parents=True, exist_ok=True)
        if not WORKOUTS.exists():
            WORKOUTS.write_text("# Training Log\n")
        effort = tool_input.get("effort_1_to_10")
        effort_txt = f" (effort {effort}/10)" if effort else ""
        entry = f"- **{today}** — {tool_input['activity']}: {tool_input['details']}{effort_txt}\n"
        with WORKOUTS.open("a") as f:
            f.write(entry)
        return f"Workout logged to training log: {entry.strip()}"

    if name == "log_meal":
        DATA.mkdir(parents=True, exist_ok=True)
        if not MEALS.exists():
            MEALS.write_text("# Food Log\n")
        now = datetime.now().strftime("%H:%M")
        protein = tool_input.get("estimated_protein_g")
        protein_txt = f", ~{protein}g protein" if protein else ""
        entry = (
            f"- **{today} {now}** — {tool_input['description']}: "
            f"~{tool_input['estimated_calories']} kcal{protein_txt}\n"
        )
        with MEALS.open("a") as f:
            f.write(entry)
        return f"Meal logged: {entry.strip()}. (Give him today's running total if useful.)"

    if name == "save_note":
        # Notes get their own section. Appending to the end of the file used to
        # file them under whatever section came last (goals ended up under Safety).
        DATA.mkdir(parents=True, exist_ok=True)
        text = PROFILE.read_text() if PROFILE.exists() else ""
        notes = re.search(rf"## {NOTES_SECTION}\n(.*?)(?=\n## |\Z)", text, re.S)
        lines = (notes.group(1).strip() + "\n" if notes else "") + f"- ({today}) {tool_input['note']}"
        PROFILE.write_text(upsert_section(text, NOTES_SECTION, lines))
        return "Note saved to Judd's profile — future sessions will see it. " + intake_status()

    if name == "save_profile":
        DATA.mkdir(parents=True, exist_ok=True)
        existing = PROFILE.read_text() if PROFILE.exists() else ""
        PROFILE.write_text(upsert_section(existing, tool_input["section"], tool_input["content"]))
        return f"Section '{tool_input['section']}' saved. " + intake_status()

    if name == "mark_plan_kept":
        if not PLANS.exists():
            return "No plans file yet — nothing to mark."
        lines = PLANS.read_text().splitlines()
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].startswith("-") and "[kept]" not in lines[i]:
                lines[i] += " [kept]"
                PLANS.write_text("\n".join(lines) + "\n")
                return "Plan marked kept — that's +25 XP and it counts toward his Navigator gear."
        return "The latest plan is already marked kept."

    if name == "log_weight":
        DATA.mkdir(parents=True, exist_ok=True)
        if not WEIGHTS.exists():
            WEIGHTS.write_text("# Weight Log\n")
        pounds = float(tool_input["pounds"])
        if not 60 <= pounds <= 700:
            return f"That weight ({pounds} lb) looks like a typo. Ask him to confirm before logging."
        entry = f"- **{today}** — {pounds:g} lb\n"
        with WEIGHTS.open("a") as f:
            f.write(entry)
        return f"Weigh-in logged: {pounds:g} lb on {today}. Name one thing today's number can't tell him (water, salt, timing) if he seems anxious about it."

    if name == "save_plan":
        DATA.mkdir(parents=True, exist_ok=True)
        if not PLANS.exists():
            PLANS.write_text("# If-Then Plans\n")
        trigger = tool_input["if_trigger"].strip().rstrip(".")
        action = tool_input["then_action"].strip().rstrip(".")
        entry = f"- **{today}** — If {trigger}, then {action}\n"
        with PLANS.open("a") as f:
            f.write(entry)
        return f"Plan saved: If {trigger}, then {action}. Next session opens by asking whether it was kept."

    return f"Error: unknown tool '{name}'"


# --- Memory: load what the coach knows -----------------------------------------
# The model is bad at calendar math and good at reading. So every date it sees
# carries its weekday and how long ago it was, and every trend it might quote
# (weight change, protein days) is computed here, not left for it to estimate.

def dated(iso: str) -> str:
    """'2026-09-14' -> 'Mon 2026-09-14 (9 days ago)'."""
    try:
        d = date.fromisoformat(iso)
    except ValueError:
        return iso
    ago = (date.today() - d).days
    when = {0: "today", 1: "yesterday"}.get(ago, f"{ago} days ago" if ago > 0 else f"in {-ago} days")
    return f"{d:%a} {iso} ({when})"


def with_dates(text: str) -> str:
    return DATE_RE.sub(lambda m: dated(m.group(0)), text)


def read_weights() -> list:
    if not WEIGHTS.exists():
        return []
    found = [
        {"date": m.group(1), "lb": float(m.group(2))}
        for m in WEIGHT_RE.finditer(WEIGHTS.read_text())
    ]
    return sorted(found, key=lambda w: w["date"])


def weights_between(weights: list, newest_days_ago: int, oldest_days_ago: int) -> list:
    today = date.today()
    lo, hi = today - timedelta(days=oldest_days_ago), today - timedelta(days=newest_days_ago)
    return [w["lb"] for w in weights if lo <= date.fromisoformat(w["date"]) <= hi]


def avg_weight(weights: list, newest_days_ago: int, oldest_days_ago: int):
    vals = weights_between(weights, newest_days_ago, oldest_days_ago)
    return round(sum(vals) / len(vals), 1) if vals else None


def food_by_day() -> dict:
    """{date: {meals, kcal, protein}} summed from the food log."""
    out: dict = {}
    if not MEALS.exists():
        return out
    for line in MEALS.read_text().splitlines():
        d = DATE_RE.search(line)
        if not d:
            continue
        day = out.setdefault(d.group(0), {"meals": 0, "kcal": 0, "protein": 0})
        day["meals"] += 1
        day["kcal"] += sum(int(k) for k in KCAL_RE.findall(line))
        day["protein"] += sum(int(g) for g in PROTEIN_RE.findall(line))
    return out


def protein_by_day() -> dict:
    """{date: grams}. Days with meals but no protein estimate count as 0."""
    return {d: f["protein"] for d, f in food_by_day().items()}


def load_profile() -> str:
    return PROFILE.read_text().strip() if PROFILE.exists() else ""


def load_recent_workouts() -> str:
    if not WORKOUTS.exists():
        return ""
    return with_dates("\n".join(WORKOUTS.read_text().splitlines()[-10:]))


# The main lifts, by the words the log uses for them, minus variants that move a
# different weight: a 40 lb split squat logged as his squat reads as a collapse
# from 185. Anything else is left out: better no line than a wrong one.
DB = r"dumbbell|\bdbs?\b|kettlebell|\bkb\b|machine|smith|cable|band"
MAIN_LIFTS = [   # (label, matches, but not)
    ("Incline press", r"incline", r"walk|treadmill|machine|smith|cable"),
    ("Bench press", r"bench", DB + r"|incline|decline|close|dip|step"),
    ("Squat", r"squat", DB + r"|split|bulgarian|goblet|front|hack|jump|pistol|wall|air|bodyweight"),
    ("RDL", r"\brdl\b|romanian", DB + r"|single|one[- ]leg"),
    ("Deadlift", r"deadlift", DB + r"|trap|hex|stiff|single|sumo"),
    ("Overhead press", r"overhead|\bohp\b", DB + r"|landmine|seated|tricep|extension"),
    ("Lat pulldown", r"pulldown", r"straight|single|one[- ]arm"),
    ("Row", r"\brows?\b", DB + r"|chest|seated|t-bar|upright|inverted|single|one[- ]arm"),
]
SET_RE = re.compile(r"(\d{2,3})s?\s?x\s?(\d+)")   # "155x5x3", "50s x10x3" -> load, reps
# How he types it, and how the workout card logs it: "3x8 @140 lb", "4x8-10 @ 140 lb"
# (bottom of the range), "3x10 each leg @40 lb" -> reps, load. Not "@ RPE 8" or "@ 70%".
AT_LOAD_RE = re.compile(r"\d+\s?x\s?(\d+)(?:\s?[-–]\s?\d+)?[^;,@\d]*?(?:@|\bat\b)\s*(\d{2,3})(?![\d%])")


def lift_history(path: Path = None) -> dict:
    """{lift: [(date, load, reps), ...]} from every logged workout, oldest first.
    The training log in the prompt shows only the last 10 workouts; this keeps
    where he started once those roll out of view. path: another log (the scorecard)."""
    out: dict = {}
    path = path or WORKOUTS
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        d = DATE_RE.search(line)
        if not d:
            continue
        for item in re.split(r"[;,]", line.split("—", 1)[-1]):
            at = AT_LOAD_RE.search(item)
            m = at or SET_RE.search(item)
            name = item[:m.start()].lower().split(":")[-1] if m else ""   # skip a "Lower — squat focus:" title
            lift = next((label for label, match, but_not in MAIN_LIFTS
                         if re.search(match, name) and not re.search(but_not, name)), None)
            if lift:
                load, reps = (at.group(2), at.group(1)) if at else (m.group(1), m.group(2))
                out.setdefault(lift, []).append((d.group(0), int(load), int(reps)))
    return out


def load_lift_history() -> str:
    lines = []
    for lift, sets in lift_history().items():
        first, latest = sets[0], sets[-1]
        if len(sets) == 1:
            lines.append(f"- {lift}: {first[1]}x{first[2]} on {dated(first[0])}; 1 session logged")
            continue
        best = max(sets, key=lambda s: (s[1], s[2]))
        lines.append(
            f"- {lift}: first {first[1]}x{first[2]} on {dated(first[0])}; best {best[1]}x{best[2]} "
            f"on {dated(best[0])}; latest {latest[1]}x{latest[2]} on {dated(latest[0])}; {len(sets)} sessions logged"
        )
    return "\n".join(lines)


def load_weight_summary() -> str:
    weights = read_weights()
    if not weights:
        return ""
    lines = [f"- {dated(w['date'])}: {w['lb']:g} lb" for w in weights[-20:]]
    first, last = weights[0], weights[-1]
    span = (date.fromisoformat(last["date"]) - date.fromisoformat(first["date"])).days
    lbs = [w["lb"] for w in weights]
    lines.append(
        f"First to latest: {first['lb']:g} -> {last['lb']:g} lb = {last['lb'] - first['lb']:+.1f} lb "
        f"over {span} days ({len(weights)} weigh-ins). Range {min(lbs):g}-{max(lbs):g} lb."
    )
    this_week, last_week = weights_between(weights, 0, 6), weights_between(weights, 7, 13)
    if this_week and last_week:
        lines.append(
            f"Average of the last 7 days: {sum(this_week) / len(this_week):.1f} lb ({len(this_week)} weigh-ins); "
            f"the 7 days before: {sum(last_week) / len(last_week):.1f} lb ({len(last_week)})."
        )
    if span < 14:
        lines.append("Under two weeks of weigh-ins: too early to call a trend. Single readings swing 1-3 lb with water.")
    return "\n".join(lines)


def load_food_summary(days: int = FOOD_DAYS_TO_LOAD) -> str:
    food = food_by_day()
    if not food:
        return ""
    lines, logged = [], []
    for i in range(days):
        d = (date.today() - timedelta(days=i)).isoformat()
        f = food.get(d)
        if f:
            logged.append((d, f))
            lines.append(f"- {dated(d)}: {f['meals']} meal(s), ~{f['kcal']} kcal, ~{f['protein']} g protein")
        else:
            lines.append(f"- {dated(d)}: nothing logged")
    if logged:
        hit = sum(1 for _, f in logged if f["protein"] >= PROTEIN_TARGET_G)
        best_day, best = max(logged, key=lambda x: x[1]["protein"])
        lines.append(
            f"Protein target {PROTEIN_TARGET_G} g reached on {hit} of {len(logged)} logged days; "
            f"best was ~{best['protein']} g on {dated(best_day)}. These are only the meals he "
            "reported, so a low day may be a partly logged day."
        )
    return "\n".join(lines)


def load_todays_meals() -> str:
    if not MEALS.exists():
        return ""
    today = date.today().isoformat()
    lines = [l for l in MEALS.read_text().splitlines() if today in l]
    return "\n".join(lines)


def load_active_plan() -> str:
    """The most recent if-then plan from data/plans.md (written by save_plan)."""
    if not PLANS.exists():
        return ""
    lines = [l for l in PLANS.read_text().splitlines() if l.startswith("-")]
    return lines[-1] if lines else ""


def load_recent_logs() -> str:
    """The last few session transcripts. The date lives only in the file name,
    so it goes on as a heading; without it the model guessed which day was which."""
    if not LOG_DIR.exists():
        return ""
    files = sorted(LOG_DIR.glob("*.md"))[-RECENT_LOGS_TO_LOAD:]
    return "\n\n".join(
        f"### {dated(f.stem)}\n"
        + TOOL_MARKER_RE.sub("", f.read_text().strip()).replace("### Session", "#### Session")
        for f in files
    )


# Frozen instructions: the same bytes on every call, so they're cached (see
# chat() in app.py). Nothing that changes day to day goes in here, not even the date.
COACH_INSTRUCTIONS = f"""You are Judd's personal health coach — a motivational-interviewing \
practitioner: sharp, warm, evidence-based, zero fluff. You coach a real person over \
time — you remember, you follow up, you keep it sustainable. Concise, conversational \
replies; at most 1-3 small next actions.

## How you coach (MI — this is your method, not a vibe)
- Open questions over advice. Elicit HIS reasons for change ("what would being \
stronger actually make possible for you?") — change talk comes from his mouth, not yours.
- Reflect before you add: show him you heard what he said, then build on it.
- Affirm specifics — effort, consistency, choices — never generic cheerleading.
- Roll with resistance: if he pushes back, get curious about the objection. Never \
argue him into compliance; he owns the decisions.
- **Never miss twice:** when he reports a lapse or you see a gap in his logs, \
normalize it first — habit research (Lally et al.) shows a single missed day does \
not derail habit formation. Zero guilt, zero lectures. Then offer exactly ONE tiny \
next action to be the "second day that didn't slip."
- **Immediate rewards:** every time he logs a behavior, name one benefit he gets \
TODAY (post-lift mood, satiety, better sleep tonight) — long-term outcomes alone \
don't drive habits; felt rewards do.
- **Fresh starts:** after a below-target stretch, frame Monday or the new month as \
a clean slate — a reset, never a make-up or a debt.
- **Let skipped questions go:** if his reply is about something else, he saw your \
question and passed on it. Don't ask it again this session, not even as "still \
need…". You may ask once more in a later session, then drop it for good. Never \
count your asks ("third ask", "last time I'll ask"). A missing number never blocks \
coaching: work from what you have and say once what you're assuming. The exception \
is a red-flag symptom (see Safety below).
- **If-then plans:** close every session by locking exactly one implementation \
intention — "If [time/context], then [specific action]" — concrete enough to \
picture. Save it with the save_plan tool. Open each session by checking the previous plan: kept or missed, pure \
curiosity, no judgment either way.

Sharp, not saccharine: you're a coach who respects him, not a hype machine.

## Your coaching knowledge (the science you program and explain from)
- Fat-loss hierarchy: the DIET creates the deficit; LIFTING preserves muscle so \
the loss is fat; CARDIO adds expenditure; daily STEPS quietly multiply it. Never \
let him try to out-cardio a diet.
- Protein: 1.6-2.4 g/kg bodyweight daily. When you know his weight, sanity-check \
his {PROTEIN_TARGET_G} g target against that range and say so.
- Sleep is a training variable: short sleep costs strength, drives hunger hormones \
the wrong way, and blunts fat loss. Treat it like a lift.
- Weekly floors (ACSM): ≥150 min moderate cardio + ≥2 resistance days. Program \
toward at least this; his week plan builds on it.
- When he asks "why" about anything you programmed, give the one-line reason \
("Zone-2 today because it adds expenditure without stealing from tomorrow's lift").
- Medical anything — symptoms, pain, meds, conditions: screen, keep programming \
conservative, and refer out in plain language. You never diagnose.
- Never suggest a movement his profile says aggravates an injury, in any variant \
(a split squat to a box is still a split squat). Offer alternatives that avoid the pattern.

You have tools: log workouts when he reports them, log meals (estimating calories \
and protein yourself from his description), and save lasting facts to his profile. \
Use them naturally — don't announce the mechanics, just confirm what you did, and \
keep a rough running calorie total when he logs food.

Safety — non-negotiable: you are a coach, not a doctor. Flag red-flag symptoms \
(chest pain, fainting, severe/persistent pain, disordered-eating signs) and tell \
him to see a professional. Never prescribe extreme restriction or unsafe loads.

## Getting facts right
- Every number, date and trend you state comes from his logs below. If they don't \
hold the answer, say so and ask. Never fill the gap with a guess.
- Dates in the logs carry the weekday and how many days ago they were. Count spans \
from those ("8 days", not "three weeks"), and do the arithmetic before you project \
a goal date.
- Weight: quote the computed change and averages in his weight log, not one \
weigh-in. If it says it's too early to call a trend, say that.
- He agreed to a plan only if it's the saved one under "His current if-then plan" \
or he said yes in his own words. Anything you proposed that he never answered was \
not agreed: ask again, don't say "we locked it".
- When he asks about today's workout, go by "Today's session" below: it's what his \
Workout tab shows. Don't write a different session in chat."""


NO_WEIGHT_YET = (f"No weigh-ins yet. Nothing waits on one: his {PROTEIN_TARGET_G} g protein target "
                 "stands until a weigh-in lets you check it. Ask for a morning weigh-in once, then let him bring it.")
NO_LIFTS_YET = "No main lifts logged yet. His first logged session sets the starting weights, so nothing waits on his current numbers."


def build_system_prompt(todays_session: str = "") -> list:
    """The system prompt as up to three blocks, ordered from never-changes to
    changes-with-every-log, with a cache breakpoint after each of the first two:
      1. the coaching instructions (frozen),
      2. his profile and recent sessions (stable within a session),
      3. today: date, plan, today's session card, and the training, weight and
         food logs (changes whenever a tool logs something).
    A change in one block only re-bills the blocks after it."""
    cached = {"type": "ephemeral"}
    blocks = [{"type": "text", "text": COACH_INSTRUCTIONS, "cache_control": cached}]

    stable = ""
    profile = load_profile()
    if profile:
        stable += f"## What you know about Judd\n{profile}"

    gaps = profile_gaps()
    if gaps:
        stable += (
            f"\n\n## Intake still open — sections missing: {', '.join(gaps)}\n"
            "Run a real consultation, ONE topic per exchange, woven naturally into "
            "the conversation — never a question wall. After covering a topic, save "
            "it with save_profile. Never re-ask anything already in the profile or "
            "notes — consolidate known info into sections yourself.\n"
            "- 'Safety' first if missing: a plain PAR-Q-style screen (heart "
            "condition? chest pain or dizziness with exertion? joint problems? "
            "medications that matter for training?). Any flag → recommend medical "
            "clearance plainly and keep all programming conservative.\n"
            "- 'Training History' includes past attempts and why they failed.\n"
            "- 'Schedule & Equipment' means TRUE availability — the week he'll "
            "actually keep, not the aspirational one.\n"
            "- 'Lifestyle' covers sleep, stress, job activity, steps, and "
            "alcohol/social life — meet drinking and partying with harm-reduction "
            "planning (timing, food choices around it, hydration), never lectures.\n"
            "- 'Nutrition Habits': likes, dislikes, allergies, current pattern.\n"
            "- 'Readiness': importance 1-10 and confidence 1-10, plus what would "
            "move each up one point.\n"
            "If this is a brand-new user (no profile at all), welcome him and start "
            "with Safety — and mention setup earns his first XP."
        )

    logs = load_recent_logs()
    if logs:
        stable += f"\n\n## Recent sessions\n{logs}"
    if stable.strip():
        blocks.append({"type": "text", "text": stable.strip(), "cache_control": cached})

    today = f"Today is {date.today().strftime('%A, %Y-%m-%d')}."

    plan = load_active_plan()
    if plan:
        today += (
            f"\n\n## His current if-then plan (open the session by checking in on "
            f"it — kept or missed, pure curiosity, zero judgment)\n{with_dates(plan)}"
        )

    if todays_session:
        today += f"\n\n## Today's session (what his Workout tab shows)\n{todays_session}"

    workouts = load_recent_workouts()
    if workouts:
        today += f"\n\n## Recent training log\n{workouts}"

    # Before the first weigh-in or lift these say so. With nothing there, the coach
    # decided it "can't set protein" without his weight and asked in 4-5 replies
    # before the day-2 weigh-in (simulation runs 8, 9, 11).
    lifts = load_lift_history()
    today += f"\n\n## Lift history (every logged session, not just the recent ones)\n{lifts or NO_LIFTS_YET}"

    weights = load_weight_summary()
    today += f"\n\n## Weight log\n{weights or NO_WEIGHT_YET}"

    food = load_food_summary()
    if food:
        today += f"\n\n## Food log, last {FOOD_DAYS_TO_LOAD} days\n{food}"

    meals = load_todays_meals()
    if meals:
        today += f"\n\n## Today's meals (keep the running calorie total in mind)\n{meals}"

    blocks.append({"type": "text", "text": today})
    return blocks


# --- THE AGENT LOOP — the most important 30 lines in this file -----------------
def get_coach_reply(client: anthropic.Anthropic, system_prompt: str, history: list) -> None:
    """One coach turn. The model may reply with text, or ask to run tools —
    in which case we execute them, hand back results, and let it continue.
    Loop until it stops asking for tools."""
    while True:
        with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=system_prompt,
            messages=history,
            tools=TOOLS,
            # Adaptive thinking ON: with tools in play, letting the model think
            # briefly makes tool calls reliable. effort=low keeps it snappy/cheap.
            thinking={"type": "adaptive"},
            output_config={"effort": "low"},
            # Caches the conversation so far; the next call re-reads it at 0.1x.
            cache_control={"type": "ephemeral"},
        ) as stream:
            for text in stream.text_stream:
                print(text, end="", flush=True)
            response = stream.get_final_message()

        # Append the FULL assistant content (text + tool_use + thinking blocks).
        # The API is stateless — it only "remembers" what we send back.
        history.append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            return  # plain reply — turn is over

        # The model asked to run tools: execute each, collect results.
        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                print(f"\n  ⚙ {block.name}({json.dumps(block.input)})", flush=True)
                result = execute_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,  # must match the request's id
                    "content": result,
                })

        # Results go back as a *user* message; the loop calls the API again
        # so the model can react ("Logged it — nice work on the bench").
        history.append({"role": "user", "content": tool_results})
        print()


# A reply that says it logged something in a turn where no tool ran saved nothing
# (control run 13, day 4: "Logged: 3 slices pepperoni + a Monster", no log_meal).
# app.py's chat loop catches that claim and sends LOG_CHECK once. Matched only at
# the start of a sentence, so "you logged 4 weigh-ins" doesn't count.
LOG_CLAIM_RE = re.compile(r"(?:^|[.!?:]\s+|\n)\W{0,3}(?:I(?:'ve| have)? )?logged\b", re.I)
LOG_CHECK = ("App check, not from Judd: your reply says something was logged, but no tool ran this turn, "
             "so nothing was saved. If his last message reported a workout, meal or weigh-in, call the right "
             "log tool now, then reply with just \"Saved.\" If it didn't, reply with just \"All set.\"")


# --- Persist the session --------------------------------------------------------
def turn_to_text(turn: dict):
    """Flatten a history turn (string or content blocks) into readable text."""
    content = turn["content"]
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        btype = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
        # Tool calls are left out on purpose. Saved as "[log_workout] Logged…",
        # the coach read them back next session and started typing the marker
        # instead of calling the tool: it said "Logged" and saved nothing.
        # What got saved is in the training, weight and food logs anyway.
        if btype == "text":
            text = getattr(block, "text", "") or block.get("text", "")
            if text == LOG_CHECK:
                return None  # the app's check, not something he said
            parts.append(text)
        elif btype == "tool_result":
            return None  # machine chatter — skip in the human-readable log
    return " ".join(p for p in parts if p) or None


def save_session(history: list, ended: bool = True) -> None:
    """Append the session to today's log. ended=False when the conversation is
    only being rolled over and he's about to keep talking."""
    if not history:
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"{date.today().isoformat()}.md"
    lines = [f"\n### Session {datetime.now().strftime('%H:%M')}"]
    for turn in history:
        text = turn_to_text(turn)
        if text is None:
            continue
        who = "Judd" if turn["role"] == "user" else "Coach"
        lines.append(f"- **{who}:** {text}")
    # Say it out loud: a proposal left hanging at the end of a session read as
    # agreed the next day ("yesterday we locked yogurt + banana" — he never answered).
    if ended and history[-1]["role"] == "assistant":
        lines.append("- *(Session ended here: he didn't reply to the coach's last message.)*")
    with log_file.open("a") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\n(Session saved to {log_file} — I'll remember this next time.)")   # DATA_DIR may sit outside HERE


# --- The app ---------------------------------------------------------------------
def main() -> None:
    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("No ANTHROPIC_API_KEY found — copy .env.example to .env and add your key.")
        sys.exit(1)

    client = anthropic.Anthropic()
    system_prompt = build_system_prompt()
    history: list = []

    print("Health Coach v2 (with tools) — type 'quit' to end and save.\n")
    history.append({"role": "user", "content": "Hi, I'm here for today's check-in."})

    try:
        while True:
            print("Coach: ", end="", flush=True)
            get_coach_reply(client, system_prompt, history)
            print("\n")

            user = input("You: ").strip()
            if user.lower() in {"quit", "exit", "q"}:
                break
            if not user:
                continue
            history.append({"role": "user", "content": user})
    except (KeyboardInterrupt, EOFError):
        pass
    finally:
        save_session(history)


if __name__ == "__main__":
    main()
