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
from datetime import date, datetime
from pathlib import Path

import anthropic

# --- Config ------------------------------------------------------------------
MODEL = "claude-opus-5"
MAX_TOKENS = 4096  # caps thinking + reply together, so leave headroom

HERE = Path(__file__).parent
DATA = Path(os.environ.get("DATA_DIR") or HERE / "data")   # deploys point this at a persistent volume
PROFILE = DATA / "profile.md"
WORKOUTS = DATA / "workouts.md"
MEALS = DATA / "meals.md"
PLANS = DATA / "plans.md"   # written by the save_plan tool; read by the coach + Journey tab
LOG_DIR = DATA / "log"
RECENT_LOGS_TO_LOAD = 3


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
        DATA.mkdir(parents=True, exist_ok=True)
        if not PROFILE.exists():
            PROFILE.write_text("# Judd's Profile\n\n## Notes the coach saved\n")
        with PROFILE.open("a") as f:
            f.write(f"- ({today}) {tool_input['note']}\n")
        return "Note saved to Judd's profile — future sessions will see it."

    if name == "save_profile":
        DATA.mkdir(parents=True, exist_ok=True)
        existing = PROFILE.read_text() if PROFILE.exists() else ""
        PROFILE.write_text(upsert_section(existing, tool_input["section"], tool_input["content"]))
        remaining = profile_gaps()
        return (
            f"Section '{tool_input['section']}' saved. "
            + (f"Intake sections still open: {', '.join(remaining)} — cover ONE of these "
               f"next time it fits naturally, not all at once." if remaining
               else "Intake complete — the full consultation is on file.")
        )

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
        weight_file = DATA / "weight.md"        # the Progress chart reads data/weight.md
        if not weight_file.exists():
            weight_file.write_text("# Weight Log\n")
        pounds = float(tool_input["pounds"])
        if not 60 <= pounds <= 700:
            return f"That weight ({pounds} lb) looks like a typo. Ask him to confirm before logging."
        entry = f"- **{today}** — {pounds:g} lb\n"
        with weight_file.open("a") as f:
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
def load_profile() -> str:
    return PROFILE.read_text().strip() if PROFILE.exists() else ""


def load_recent_workouts() -> str:
    if not WORKOUTS.exists():
        return ""
    return "\n".join(WORKOUTS.read_text().splitlines()[-10:])


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
    if not LOG_DIR.exists():
        return ""
    files = sorted(LOG_DIR.glob("*.md"))[-RECENT_LOGS_TO_LOAD:]
    return "\n\n".join(f.read_text().strip() for f in files)


def build_system_prompt() -> str:
    prompt = f"""You are Judd's personal health coach — a motivational-interviewing \
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
his 175g target against that range and say so.
- Sleep is a training variable: short sleep costs strength, drives hunger hormones \
the wrong way, and blunts fat loss. Treat it like a lift.
- Weekly floors (ACSM): ≥150 min moderate cardio + ≥2 resistance days. Program \
toward at least this; his week plan builds on it.
- When he asks "why" about anything you programmed, give the one-line reason \
("Zone-2 today because it adds expenditure without stealing from tomorrow's lift").
- Medical anything — symptoms, pain, meds, conditions: screen, keep programming \
conservative, and refer out in plain language. You never diagnose.

You have tools: log workouts when he reports them, log meals (estimating calories \
and protein yourself from his description), and save lasting facts to his profile. \
Use them naturally — don't announce the mechanics, just confirm what you did, and \
keep a rough running calorie total when he logs food.

Safety — non-negotiable: you are a coach, not a doctor. Flag red-flag symptoms \
(chest pain, fainting, severe/persistent pain, disordered-eating signs) and tell \
him to see a professional. Never prescribe extreme restriction or unsafe loads.

Today is {date.today().strftime("%A, %Y-%m-%d")}."""

    profile = load_profile()
    if profile:
        prompt += f"\n\n## What you know about Judd\n{profile}"

    gaps = profile_gaps()
    if gaps:
        prompt += (
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

    plan = load_active_plan()
    if plan:
        prompt += (
            f"\n\n## His current if-then plan (open the session by checking in on "
            f"it — kept or missed, pure curiosity, zero judgment)\n{plan}"
        )

    workouts = load_recent_workouts()
    if workouts:
        prompt += f"\n\n## Recent training log\n{workouts}"

    meals = load_todays_meals()
    if meals:
        prompt += f"\n\n## Today's food log (keep the running calorie total in mind)\n{meals}"

    logs = load_recent_logs()
    if logs:
        prompt += f"\n\n## Recent sessions\n{logs}"

    return prompt


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


# --- Persist the session --------------------------------------------------------
def turn_to_text(turn: dict):
    """Flatten a history turn (string or content blocks) into readable text."""
    content = turn["content"]
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        btype = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
        if btype == "text":
            parts.append(getattr(block, "text", "") or block.get("text", ""))
        elif btype == "tool_use":
            parts.append(f"[{getattr(block, 'name', '') or block.get('name', 'tool')}]")
        elif btype == "tool_result":
            return None  # machine chatter — skip in the human-readable log
    return " ".join(p for p in parts if p) or None


def save_session(history: list) -> None:
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
