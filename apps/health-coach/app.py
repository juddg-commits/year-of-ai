#!/usr/bin/env python3
"""
Health Coach — Web App (v6): hardened + full game layer.

coach.py is the ENGINE (prompt, tools, memory). This file is the API + GAME:
  POST /chat, GET /history           — conversation (with rollback-on-failure)
  GET  /progress                     — behavior metrics (streak now shield-aware)
  GET  /game, POST related           — XP, levels, avatar, gear, quests, chests
  GET  /workout, /workout/complete   — AI-programmed session (idempotent logging)
  /manifest.json /sw.js /icon.svg    — PWA so it installs to a phone

GAME DESIGN PRINCIPLE (Judd's rule #1, enforced by architecture):
XP is *derived from the log files* on every request — never stored as a counter.
There is no code path that can mint XP from an app open or a chat message,
because XP isn't a thing that gets "added": it's a *view over real behavior*.
game.json stores only what can't be derived: quest state, chest history, and
which rewards have already been announced (so juice plays once).
"""

import atexit
import hashlib
import hmac
import json
import os
import random
import re
import threading
import time

import httpx
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import anthropic
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from urllib.parse import parse_qs
from pydantic import BaseModel, Field

from coach import (
    DATA,
    MAX_TOKENS,
    MEALS,
    MODEL,
    PLANS,
    TOOLS,
    WORKOUTS,
    build_system_prompt,
    execute_tool,
    load_active_plan,
    load_dotenv,
    load_profile,
    load_recent_workouts,
    save_session,
)

load_dotenv()
if os.environ.get("TZ"):
    time.tzset()   # servers run on UTC; "today" must be YOUR today or logs land on tomorrow
client = anthropic.Anthropic()
# No /docs: its "Try it out" buttons would be one more way to fire paid calls.
app = FastAPI(title="Health Coach", docs_url=None, redoc_url=None, openapi_url=None)


# ── Password gate: only you (and your API bill) get in ────────────────────────
# Set APP_PASSWORD on the server. Unset (local dev) → the app stays open.
# The cookie is an HMAC of the password, so changing the password logs out
# every device, and there is no session store to lose on a redeploy.

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
SESSION_COOKIE = "coach_session"
PUBLIC_PATHS = {"/login", "/healthz", "/manifest.json", "/sw.js", "/icon.svg",
                "/icon-192.png", "/icon-512.png", "/apple-touch-icon.png"}
LOGIN_WINDOW_S, LOGIN_MAX_TRIES, LOGIN_MAX_GLOBAL = 15 * 60, 8, 40
login_attempts: dict = {}   # {ip or "*": [timestamps]} — slows password guessing


def session_token() -> str:
    return hmac.new(APP_PASSWORD.encode(), b"coach-session-v1", hashlib.sha256).hexdigest()


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if path in PUBLIC_PATHS:
        return await call_next(request)
    if APP_PASSWORD and not hmac.compare_digest(request.cookies.get(SESSION_COOKIE, ""), session_token()):
        if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
            return RedirectResponse("/login", status_code=303)
        return JSONResponse({"error": "login_required"}, status_code=401)
    # Every API call must carry X-Coach. Other websites can't add custom headers
    # to cross-site requests, so a link or form elsewhere can't fire a paid call.
    if path != "/" and request.headers.get("x-coach") != "1":
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return await call_next(request)


def login_page(error: str = "") -> HTMLResponse:
    html = (HERE / "login.html").read_text().replace("{{error}}", error)
    return HTMLResponse(html, status_code=401 if error else 200)


@app.get("/login", response_class=HTMLResponse)
def login_form() -> HTMLResponse:
    return login_page()


@app.post("/login")
async def login(request: Request):
    # Rightmost X-Forwarded-For entry is the one the host's proxy added; earlier
    # entries are whatever the client claimed. The "*" bucket caps all IPs together.
    ip = (request.headers.get("x-forwarded-for") or request.client.host).split(",")[-1].strip()
    now = time.time()
    recent = [t for t in login_attempts.get(ip, []) if now - t < LOGIN_WINDOW_S]
    everyone = [t for t in login_attempts.get("*", []) if now - t < LOGIN_WINDOW_S]
    if len(recent) >= LOGIN_MAX_TRIES or len(everyone) >= LOGIN_MAX_GLOBAL:
        return login_page("Too many tries. Wait 15 minutes and try again.")
    password = parse_qs((await request.body()).decode()).get("password", [""])[0]
    if not APP_PASSWORD or not hmac.compare_digest(password.encode(), APP_PASSWORD.encode()):
        login_attempts[ip] = recent + [now]
        login_attempts["*"] = everyone + [now]
        return login_page("Wrong password.")
    login_attempts.pop(ip, None)
    resp = RedirectResponse("/", status_code=303)
    https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    resp.set_cookie(SESSION_COOKIE, session_token(), max_age=365 * 24 * 3600,
                    httponly=True, samesite="lax", secure=https)
    return resp


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}

HERE = Path(__file__).parent
WEIGHT_FILE = DATA / "weight.md"   # Judd's log_weight tool writes this (his build)
GAME_FILE = DATA / "game.json"
PROTEIN_TARGET_G = 175

history: list = []
workout_cache: dict = {}
workout_seen: dict = {}   # {iso_date: [titles shown]} — "Different workout" never circles back
chat_lock = threading.Lock()   # one coach turn at a time — no interleaved history
game_lock = threading.Lock()   # game.json read-modify-write safety

atexit.register(lambda: save_session(history))

# Tools whose successful use == a real logged behavior (chest-roll eligible).
LOGGING_TOOLS = {"log_workout", "log_meal", "log_weight", "mark_plan_kept"}

# ── game.json state (only the non-derivable bits) ─────────────────────────────

DEFAULT_GAME = {
    "announced_level": 0,
    "announced_gear": [],
    "announced_trophies": [],
    "quests": {"date": "", "items": [], "sweep_claimed": False},
    "bonus_xp": [],            # [{date, amount, reason}] — quests + chests only
    "workout_completed": {},   # {iso_date: title} — idempotency for /workout/complete
}


def load_game() -> dict:
    if GAME_FILE.exists():
        try:
            stored = json.loads(GAME_FILE.read_text())
            return {**DEFAULT_GAME, **stored}
        except (json.JSONDecodeError, OSError):
            pass  # corrupted file → fresh state beats a crashed app
    return json.loads(json.dumps(DEFAULT_GAME))


def save_game(g: dict) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    GAME_FILE.write_text(json.dumps(g, indent=2))


# ── Parsing the logs (source of truth for everything) ─────────────────────────

DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
WEIGHT_RE = re.compile(r"(\d{4}-\d{2}-\d{2}).*?(\d+(?:\.\d+)?)\s*lb")
PROTEIN_RE = re.compile(r"~(\d+)\s*g protein")


def dates_of(path: Path) -> list:
    return DATE_RE.findall(path.read_text()) if path.exists() else []


def read_weights() -> list:
    if not WEIGHT_FILE.exists():
        return []
    found = [
        {"date": m.group(1), "lb": float(m.group(2))}
        for m in WEIGHT_RE.finditer(WEIGHT_FILE.read_text())
    ]
    return sorted(found, key=lambda w: w["date"])


def protein_by_day() -> dict:
    """{date: grams} summed from the food log."""
    out: dict = {}
    if not MEALS.exists():
        return out
    for line in MEALS.read_text().splitlines():
        d = DATE_RE.search(line)
        if not d:
            continue
        for g in PROTEIN_RE.findall(line):
            out[d.group(0)] = out.get(d.group(0), 0) + int(g)
    return out


def plans_kept_count() -> int:
    return PLANS.read_text().count("[kept]") if PLANS.exists() else 0


def logged_dates() -> set:
    # Real behavior logs only. Chat-session transcripts don't count: the page
    # auto-greets on open, so counting them let an app open extend the streak.
    dates = set()
    for f in (MEALS, WORKOUTS, WEIGHT_FILE, PLANS):
        if f.exists():
            dates.update(DATE_RE.findall(f.read_text()))
    return dates


# ── Streak with SHIELD: one automatic rest-day pass per week ──────────────────

def streak_info(extra: frozenset = frozenset()) -> dict:
    """Consecutive logged days, where ONE isolated missed day per ISO-week is
    bridged automatically (🛡). Rest is never punished; passes don't stack —
    a second miss in the same week ends the run honestly. `extra` lets the
    stakes preview ask "what would the streak be if today were logged?"."""
    dates = logged_dates() | extra
    day = date.today()
    if day.isoformat() not in dates:
        day -= timedelta(days=1)  # an unlogged morning isn't a miss yet

    streak, used_weeks = 0, set()
    while True:
        if day.isoformat() in dates:
            streak += 1
            day -= timedelta(days=1)
            continue
        week = day.isocalendar()[:2]
        prev_logged = (day - timedelta(days=1)).isoformat() in dates
        if prev_logged and week not in used_weeks:
            used_weeks.add(week)       # shield absorbs one isolated rest day
            day -= timedelta(days=1)
            continue
        break

    this_week = date.today().isocalendar()[:2]
    return {"streak": streak, "shield_ready": this_week not in used_weeks}


def max_streak_ever() -> int:
    ds = sorted(date.fromisoformat(d) for d in logged_dates())
    best = run = 0
    for i, d in enumerate(ds):
        run = run + 1 if i and (d - ds[i - 1]).days == 1 else 1
        best = max(best, run)
    return best


# ── XP ENGINE: a view over the logs, never a stored counter ───────────────────

XP_RULES = {  # behavior → XP. The ONLY earning surface (+ quests/chests below).
    "workout": 50, "meal": 10, "protein_day": 30,
    "weighin": 20, "plan_kept": 25, "profile": 40,
}
TITLES = ["Rookie", "Consistent", "Athlete", "Machine", "Relentless", "Legend"]
AVATAR_MILESTONES = [1, 3, 5, 8, 12]   # level at which each avatar stage unlocks


def behavior_counts() -> dict:
    protein_days = sum(1 for g in protein_by_day().values() if g >= PROTEIN_TARGET_G)
    return {
        "workout": len(dates_of(WORKOUTS)),
        "meal": len(dates_of(MEALS)),
        "protein_day": protein_days,
        "weighin": len(read_weights()),
        "plan_kept": plans_kept_count(),
        "profile": 1 if load_profile() else 0,
    }


def xp_summary(game: dict) -> dict:
    counts = behavior_counts()
    base = sum(XP_RULES[k] * n for k, n in counts.items())
    bonus = sum(b["amount"] for b in game["bonus_xp"])
    return {"total": base + bonus, "base": base, "bonus": bonus, "counts": counts}


def level_from_xp(xp: int) -> dict:
    """Rising curve: each level costs 50 more than the last (100, 150, 200…)."""
    level, threshold, spent = 1, 100, 0
    while xp - spent >= threshold:
        spent += threshold
        level += 1
        threshold += 50
    return {
        "n": level,
        "title": TITLES[min((level - 1) // 2, len(TITLES) - 1)],
        "pct": round(100 * (xp - spent + 2) / (threshold + 2)),  # endowed: never 0
        "into": xp - spent,
        "needs": threshold,
    }


def narration_for(counts: dict) -> str:
    """Every reward names the real behavior that earned it — Judd's rule #4."""
    labels = {
        "workout": "workouts", "meal": "meals logged", "protein_day": "protein-target days",
        "weighin": "weigh-ins", "plan_kept": "plans kept", "profile": "profile set up",
    }
    singular = {
        "workout": "workout", "meal": "meal logged", "protein_day": "protein-target day",
        "weighin": "weigh-in", "plan_kept": "plan kept", "profile": "profile set up",
    }
    top = sorted(
        ((XP_RULES[k] * n, k, n) for k, n in counts.items() if n),
        reverse=True,
    )[:2]
    return " and ".join(
        f"{n} {labels[k]}" if n > 1 else (singular[k] if k == "profile" else f"1 {singular[k]}")
        for _, k, n in top
    ) or "showing up"


# ── Gear + trophies (all derived; grey until real behavior unlocks them) ──────

def protein_week_done() -> bool:
    weeks = Counter()
    for d, g in protein_by_day().items():
        if g >= PROTEIN_TARGET_G:
            weeks[date.fromisoformat(d).isocalendar()[:2]] += 1
    return any(n >= 5 for n in weeks.values())


def gear_list(counts: dict) -> list:
    return [
        {"id": "headband", "name": "Headband", "icon": "🎽",
         "need": "7-day streak", "earned": max_streak_ever() >= 7},
        {"id": "dumbbell", "name": "Dumbbell", "icon": "🏋️",
         "need": "10 workouts logged", "earned": counts["workout"] >= 10},
        {"id": "cape", "name": "Cape", "icon": "🦸",
         "need": "protein target 5 days in one week", "earned": protein_week_done()},
        {"id": "compass", "name": "Compass", "icon": "🧭",
         "need": "4 if-then plans kept", "earned": counts["plan_kept"] >= 4},
    ]


def trophy_list(counts: dict) -> list:
    ms = max_streak_ever()
    defs = [
        ("first_workout", "First Rep", "💪", "log your first workout", counts["workout"] >= 1),
        ("workouts_10", "Double Digits", "🔟", "log 10 workouts", counts["workout"] >= 10),
        ("workouts_25", "Quarter Hundred", "🏗️", "log 25 workouts", counts["workout"] >= 25),
        ("first_meal", "On the Books", "🍽️", "log your first meal", counts["meal"] >= 1),
        ("meals_50", "Food Historian", "📒", "log 50 meals", counts["meal"] >= 50),
        ("protein_first", "Protein Hit", "🥩", "hit your protein target once", counts["protein_day"] >= 1),
        ("protein_week", "Protein Week", "🧱", "protein target 5 days in one week", protein_week_done()),
        ("weighin_first", "On Record", "⚖️", "log your first weigh-in", counts["weighin"] >= 1),
        ("streak_7", "One Week Strong", "🔥", "7-day logging streak", ms >= 7),
        ("streak_14", "Fortnight", "🌋", "14-day logging streak", ms >= 14),
        ("plan_kept", "Word Kept", "🤝", "keep your first if-then plan", counts["plan_kept"] >= 1),
        ("plans_4", "Navigator", "🗺️", "keep 4 if-then plans", counts["plan_kept"] >= 4),
        ("profile", "Day One", "🚀", "complete your profile", counts["profile"] >= 1),
    ]
    return [
        {"id": i, "name": n, "icon": ic, "need": need, "earned": e}
        for i, n, ic, need, e in defs
    ]


# ── Daily quests: generated from HIS data, verified against HIS logs ──────────
# A tap cannot complete a quest — quests self-verify when the log lands
# (rule #1: XP only from real logged behavior).

def build_quests() -> list:
    today = date.today().isoformat()
    protein = protein_by_day().get(today, 0)
    meals_today = sum(1 for d in dates_of(MEALS) if d == today)
    q = []
    if today not in dates_of(WORKOUTS):
        q.append({"id": "train", "text": "If it's your training window, then start today's programmed workout"})
    if protein < PROTEIN_TARGET_G:
        q.append({"id": "protein", "text": f"If it's a meal today, then log it — target {PROTEIN_TARGET_G}g protein"})
    if WEIGHT_FILE.exists() and today not in dates_of(WEIGHT_FILE):
        q.append({"id": "weigh", "text": "If it's morning, then hop on the scale and tell the coach"})
    if meals_today < 3:
        q.append({"id": "meals", "text": "If you finish eating anything, then log it before the plate's away"})
    if load_active_plan() and "[kept]" not in load_active_plan():
        q.append({"id": "plan", "text": "If your plan's moment arrives, then run it and tell the coach"})
    return [{**x, "done": False} for x in q[:3]]


QUEST_LABELS = {  # short names for toasts — the full if-then text reads like an instruction
    "train": "Train today", "protein": f"Hit {PROTEIN_TARGET_G}g protein",
    "weigh": "Weigh in", "meals": "Log 3 meals", "plan": "Run your plan",
}


def quest_satisfied(qid: str) -> bool:
    today = date.today().isoformat()
    return {
        "train": today in dates_of(WORKOUTS),
        "protein": protein_by_day().get(today, 0) >= PROTEIN_TARGET_G,
        "weigh": today in dates_of(WEIGHT_FILE),
        "meals": sum(1 for d in dates_of(MEALS) if d == today) >= 3,
        "plan": "[kept]" in (load_active_plan() or ""),
    }.get(qid, False)


def refresh_quests(game: dict) -> list:
    """Roll to a new day if needed; verify quests against the logs; award XP
    for freshly completed ones. Returns juice events."""
    today = date.today().isoformat()
    events = []
    if game["quests"]["date"] != today:
        game["quests"] = {"date": today, "items": build_quests(), "sweep_claimed": False}

    for item in game["quests"]["items"]:
        if not item["done"] and quest_satisfied(item["id"]):
            item["done"] = True
            game["bonus_xp"].append({"date": today, "amount": 15, "reason": f"quest:{item['id']}"})
            label = QUEST_LABELS.get(item["id"], item["text"])
            events.append({"type": "quest", "text": f"✓ Quest complete: {label} · +15 XP", "xp": 15})

    items = game["quests"]["items"]
    if items and all(i["done"] for i in items) and not game["quests"]["sweep_claimed"]:
        game["quests"]["sweep_claimed"] = True
        game["bonus_xp"].append({"date": today, "amount": 20, "reason": "quest_sweep"})
        events.append({"type": "sweep", "text": "All quests cleared today · +20 XP", "xp": 20})
    return events


# ── Variable reward: honest odds, only after real logged behavior ─────────────

CHEST_CHANCE = 0.15                      # disclosed in the UI — no dark patterns
CHEST_AMOUNTS = [10, 15, 20, 25, 40]


def maybe_chest(game: dict, behavior: str) -> list:
    if random.random() >= CHEST_CHANCE:
        return []
    amount = random.choice(CHEST_AMOUNTS)
    game["bonus_xp"].append({"date": date.today().isoformat(), "amount": amount, "reason": f"chest:{behavior}"})
    return [{"type": "chest", "xp": amount,
             "text": f"Bonus chest! +{amount} XP — dropped by your {behavior} ({int(CHEST_CHANCE*100)}% chance after any log)"}]


# ── Announce-once diffing (juice plays a single time per unlock) ──────────────

def collect_announcements(game: dict) -> list:
    counts = behavior_counts()
    xp = xp_summary(game)
    level = level_from_xp(xp["total"])
    events = []

    if level["n"] > game["announced_level"]:
        if game["announced_level"]:   # don't fanfare the very first computation
            events.append({
                "type": "level", "n": level["n"], "title": level["title"],
                "text": f"Level {level['n']}: {level['title']} — earned by {narration_for(counts)}",
            })
        game["announced_level"] = level["n"]

    for g in gear_list(counts):
        if g["earned"] and g["id"] not in game["announced_gear"]:
            game["announced_gear"].append(g["id"])
            events.append({"type": "gear", "text": f"Gear unlocked: {g['icon']} {g['name']} — {g['need']}"})

    for t in trophy_list(counts):
        if t["earned"] and t["id"] not in game["announced_trophies"]:
            game["announced_trophies"].append(t["id"])
            events.append({"type": "trophy", "text": f"Trophy: {t['icon']} {t['name']} — {t['need']}"})
    return events


# ── Chat (hardened: lock, rollback, friendly errors, chest hook) ──────────────

class ChatMessage(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


MAX_TOOL_ROUNDS = 8     # a turn that keeps calling tools is a bug, not a conversation
MAX_HISTORY = 120       # messages; past this, save to memory and start a fresh context


@app.get("/", response_class=HTMLResponse)
def home() -> str:
    return (HERE / "index.html").read_text()


def _block_field(block, name: str):
    return getattr(block, name, None) if not isinstance(block, dict) else block.get(name)


@app.get("/history")
def get_history() -> dict:
    """Rehydrate the chat UI after a reload — the server remembers the session.
    Waits on chat_lock so a reload mid-reply gets the finished reply instead of
    losing it. Tool calls come back as chips on the next coach bubble, the same
    way a live turn renders them."""
    out, texts, tools = [], [], []

    def flush():
        if texts or tools:
            out.append({"role": "assistant", "text": "\n\n".join(texts), "tools": list(tools)})
        texts.clear(); tools.clear()

    with chat_lock:
        for turn in history:
            content = turn["content"]
            if turn["role"] == "user":
                if isinstance(content, str):   # a real message ends the coach's turn
                    flush()
                    out.append({"role": "user", "text": content})
                continue  # tool_result turns are machine chatter
            for block in content:
                btype = _block_field(block, "type")
                if btype == "text" and (_block_field(block, "text") or "").strip():
                    texts.append(_block_field(block, "text").strip())
                elif btype == "tool_use":
                    tools.append(_block_field(block, "name"))
        flush()
        return {"turns": out, "events": list(pending_events)}


# Celebrations wait here until the page confirms it showed them, so a reload
# mid-reply replays them instead of losing them (they're announce-once).
pending_events: list = []


@app.post("/events/ack")
def ack_events() -> dict:
    pending_events.clear()
    return {"ok": True}


@app.post("/chat")
def chat(msg: ChatMessage) -> dict:
    with chat_lock:
        if len(history) >= MAX_HISTORY:
            # Roll over rather than trim: the saved log feeds the next system
            # prompt, so the coach keeps its memory without an edited transcript.
            save_session(history)
            history.clear()
        with game_lock:   # roll to today's quests BEFORE anything gets logged
            game = load_game()
            events = refresh_quests(game)
            save_game(game)
        snapshot = len(history)
        history.append({"role": "user", "content": msg.message})
        system_prompt = build_system_prompt()
        tools_used, texts = [], []
        try:
            for _ in range(MAX_TOOL_ROUNDS):
                response = client.messages.create(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    system=system_prompt,
                    messages=history,
                    tools=TOOLS,
                    thinking={"type": "adaptive"},
                    output_config={"effort": "low"},
                )
                history.append({"role": "assistant", "content": response.content})
                # Keep text from EVERY round ("Logging that now…" + the final
                # reply) — /history shows all of it, so the live bubble must too.
                texts += [b.text.strip() for b in response.content if b.type == "text" and b.text.strip()]
                if response.stop_reason != "tool_use":
                    break
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        tools_used.append(block.name)
                        result = execute_tool(block.name, block.input)
                        tool_results.append(
                            {"type": "tool_result", "tool_use_id": block.id, "content": result}
                        )
                history.append({"role": "user", "content": tool_results})
        except Exception:
            # Roll back so a failed turn can't leave dangling tool_use blocks
            # that would poison every future request.
            del history[snapshot:]
            pending_events.extend(events)   # the day-rollover quests still happened
            return {
                "reply": "Hit a snag reaching the model — give it a second and try again.",
                "tools_used": [], "events": events, "error": True,
            }

        reply = "\n\n".join(texts) or "I logged what I could. Tell me if anything's missing."
        if set(tools_used) & LOGGING_TOOLS:
            with game_lock:
                game = load_game()
                events += refresh_quests(game)
                events += maybe_chest(game, next(iter(set(tools_used) & LOGGING_TOOLS)))
                events += collect_announcements(game)
                save_game(game)
        pending_events.extend(events)
    return {"reply": reply, "tools_used": tools_used, "events": events}


@app.post("/reset")
def reset() -> dict:
    with chat_lock:
        save_session(history)
        history.clear()
    return {"ok": True}


# ── Progress (unchanged metrics + shield-aware streak) ────────────────────────

WEEKLY_TARGET_DAYS = 5
ENDOW = 1


def week_start() -> date:
    today = date.today()
    return today - timedelta(days=today.weekday())


def habit_sources() -> dict:
    return {
        "Training": dates_of(WORKOUTS),
        "Food log": dates_of(MEALS),   # days with any meal logged, not protein-target days
        "Weigh-ins": dates_of(WEIGHT_FILE),
    }


def weekly_goals() -> list:
    start = week_start()
    goals = []
    for habit, all_dates in habit_sources().items():
        done = min(len({d for d in all_dates if date.fromisoformat(d) >= start}), WEEKLY_TARGET_DAYS)
        goals.append({
            "habit": habit, "done": done, "target": WEEKLY_TARGET_DAYS,
            "pct": round(100 * (done + ENDOW) / (WEEKLY_TARGET_DAYS + ENDOW)),
            "hot": done / WEEKLY_TARGET_DAYS >= 0.7,
        })
    return goals


def automaticity() -> list:
    cutoff = date.today() - timedelta(days=66)
    out = []
    for habit, all_dates in habit_sources().items():
        recent = {d for d in all_dates if date.fromisoformat(d) >= cutoff}
        reps = len(recent)
        if recent:
            weekdays = Counter(date.fromisoformat(d).weekday() for d in recent)
            consistency = sum(n for _, n in weekdays.most_common(3)) / reps
        else:
            consistency = 0.0
        reps_frac = min(reps, 66) / 66
        out.append({"habit": habit, "pct": round(100 * reps_frac * (0.7 + 0.3 * consistency)), "reps": reps})
    return out


def avg_weight(weights: list, newest_days_ago: int, oldest_days_ago: int):
    today = date.today()
    lo, hi = today - timedelta(days=oldest_days_ago), today - timedelta(days=newest_days_ago)
    vals = [w["lb"] for w in weights if lo <= date.fromisoformat(w["date"]) <= hi]
    return round(sum(vals) / len(vals), 1) if vals else None


def fresh_start() -> dict:
    today = date.today()
    last_start = week_start() - timedelta(days=7)
    last_week_days = {
        d for dates in habit_sources().values() for d in dates
        if last_start <= date.fromisoformat(d) < week_start()
    }
    landmark = (
        "today — it's the 1st" if today.day == 1
        else "today — it's Monday" if today.weekday() == 0
        else "Monday"
    )
    next_monday = today + timedelta(days=(7 - today.weekday()) % 7 or 7)
    return {
        "show": len(last_week_days) < 4,
        "landmark": landmark,
        "date": (today if "today" in landmark else next_monday).isoformat(),
    }


def active_plan() -> dict:
    line = load_active_plan()
    if not line:
        return {"exists": False}
    m = re.search(r"If (.+?), then (.+)", line)
    text = f"If {m.group(1)}, then {m.group(2)}" if m else line.lstrip("- ")
    d = DATE_RE.search(line)
    return {
        "exists": True,
        "text": text.replace(" [kept]", ""),
        "kept": "[kept]" in line,
        "date": d.group(0) if d else None,
    }


@app.get("/progress")
def progress() -> dict:
    weights = read_weights()
    s = streak_info()
    return {
        "weights": weights,
        "streak": s["streak"],
        "shield_ready": s["shield_ready"],
        "week": {"this": avg_weight(weights, 0, 6), "last": avg_weight(weights, 7, 13)},
        "protein": {"today": protein_by_day().get(date.today().isoformat(), 0), "target": PROTEIN_TARGET_G},
        "plan": active_plan(),
        "goals": weekly_goals(),
        "automaticity": automaticity(),
        "fresh_start": fresh_start(),
    }


# ── Game endpoint ─────────────────────────────────────────────────────────────

@app.get("/game")
def game_state() -> dict:
    with game_lock:
        game = load_game()
        events = refresh_quests(game) + collect_announcements(game)
        xp = xp_summary(game)
        level = level_from_xp(xp["total"])
        counts = xp["counts"]
        stage = sum(1 for m in AVATAR_MILESTONES if level["n"] >= m)
        next_ms = next((m for m in AVATAR_MILESTONES if level["n"] < m), None)
        save_game(game)
    return {
        "xp": xp,
        "level": level,
        "narration": narration_for(counts),
        "avatar": {"stage": stage, "next_milestone": next_ms},
        "gear": gear_list(counts),
        "trophies": trophy_list(counts),
        "quests": game["quests"],
        "chest_odds": f"{int(CHEST_CHANCE * 100)}% after any real log",
        "events": events,
    }


# ── Today's workout (hardened + idempotent completion) ────────────────────────

WORKOUT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "focus": {"type": "string"},
        "duration_minutes": {"type": "integer"},
        "kind": {"type": "string", "enum": ["lift", "cardio", "hiit", "mobility"]},
        "exercises": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "exercise": {"type": "string"},
                    "sets": {"type": "integer"},
                    "reps": {"type": "string"},
                    "rest_seconds": {"type": "integer"},
                    "notes": {"type": "string"},
                    "video_query": {
                        "type": "string",
                        "description": "cleanest YouTube search phrase for form — include a reputable coach when apt, e.g. 'romanian deadlift form Jeff Nippard'; empty for warm-ups/walks",
                    },
                },
                "required": ["exercise", "sets", "reps", "rest_seconds", "notes", "video_query"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "focus", "duration_minutes", "kind", "exercises"],
    "additionalProperties": False,
}


# ── WEEKLY PROGRAM ENGINE: the week comes first, sessions serve the week ──────

PROGRAM_FILE = DATA / "program.json"
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

PROGRAM_SCHEMA = {
    "type": "object",
    "properties": {
        "week_focus": {"type": "string", "description": "one line: this week's intent"},
        "rationale": {"type": "string", "description": "one line: why this structure, tied to the goal"},
        "step_target": {"type": "integer", "description": "daily step target"},
        "days": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "day": {"type": "string", "enum": DAY_NAMES},
                    "type": {"type": "string", "enum": ["lift", "cardio", "hiit", "rest"]},
                    "title": {"type": "string"},
                    "detail": {
                        "type": "string",
                        "description": (
                            "cardio/hiit: modality + target zone + RPE + structure "
                            "(e.g. 'incline treadmill, zone 2, RPE 5-6, steady 35 min'). "
                            "lift: the session focus. rest: recovery guidance."
                        ),
                    },
                    "duration_minutes": {"type": "integer"},
                    "video_query": {
                        "type": "string",
                        "description": "for cardio/hiit: cleanest YouTube search phrase for the session technique, e.g. 'zone 2 incline treadmill walking'; empty for lift/rest days",
                    },
                },
                "required": ["day", "type", "title", "detail", "duration_minutes", "video_query"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["week_focus", "rationale", "step_target", "days"],
    "additionalProperties": False,
}


def week_key() -> str:
    y, w, _ = date.today().isocalendar()
    return f"{y}-W{w:02d}"


def this_weeks_training_log() -> str:
    """What's already been logged since Monday — the program engine and the
    session generator both respect work already done."""
    if not WORKOUTS.exists():
        return "(nothing logged yet this week)"
    start = week_start()
    lines = [
        l for l in WORKOUTS.read_text().splitlines()
        if (m := DATE_RE.search(l)) and date.fromisoformat(m.group(0)) >= start
    ]
    return "\n".join(lines) or "(nothing logged yet this week)"


def get_program(new: int = 0) -> dict:
    """Build (or load) THIS WEEK's program from the completed profile.
    Persisted to data/program.json so the week survives server restarts."""
    key = week_key()
    if not new and PROGRAM_FILE.exists():
        try:
            stored = json.loads(PROGRAM_FILE.read_text())
            if stored.get("week") == key:
                return stored
        except (json.JSONDecodeError, OSError):
            pass
    profile = load_profile() or "(no profile yet — program a conservative beginner week)"
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=(
            "You are a strength & conditioning coach building ONE WEEK of training "
            "from a client consultation. Rules you program by:\n"
            "- Fat-loss goal: 3-4 lifting days + 2-3 cardio prescriptions (zone-2 "
            "LISS 30-40 min at RPE 5-6; at most 1 HIIT day and only if recovery "
            "allows) + a daily step target.\n"
            "- ACSM floor: ≥150 min weekly cardio + ≥2 resistance days.\n"
            "- Fit his TRUE stated availability — never program days he said he "
            "doesn't have. Unnamed days are rest.\n"
            "- Any safety flags in the profile → conservative choices.\n"
            "- Cover all 7 days (rest days included). One line of rationale."
        ),
        messages=[{"role": "user", "content":
                   f"## Client profile\n{profile}\n\n## Already logged this week\n"
                   f"{this_weeks_training_log()}\n\nToday is {date.today().strftime('%A, %Y-%m-%d')}. "
                   f"Build this week's program."}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": PROGRAM_SCHEMA}},
    )
    program = json.loads(next(b.text for b in response.content if b.type == "text"))
    program["week"] = key
    DATA.mkdir(parents=True, exist_ok=True)
    PROGRAM_FILE.write_text(json.dumps(program, indent=2))
    return program


@app.get("/program")
def program_endpoint(new: int = 0) -> dict:
    try:
        return get_program(new)
    except Exception:
        return {"error": "Couldn't build the week — try again in a moment."}


def write_session(slot_desc: str, ask: str) -> dict:
    """One model call → one structured session, grounded in his real logs."""
    profile = load_profile() or "(no profile yet — keep it conservative)"
    response = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=(
            "You are Judd's strength coach writing out today's session from his "
            "weekly program. Apply progressive overload from his logged loads "
            "where justified — reference actual numbers from his logs. Respect "
            "anything already trained this week: don't hit a muscle group "
            "trained in the last 48 hours. 5-7 exercises incl. warm-up for a "
            "lift; cardio sessions can be 2-4 blocks."
        ),
        messages=[{"role": "user", "content":
                   f"## Today's program slot\n{slot_desc}\n\n## Client profile\n{profile}\n\n"
                   f"## Already logged this week\n{this_weeks_training_log()}\n\n"
                   f"## Recent training (with loads)\n{load_recent_workouts() or '(none)'}\n\n"
                   f"Today is {date.today().isoformat()}. {ask}"}],
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": WORKOUT_SCHEMA}},
    )
    plan = json.loads(next(b.text for b in response.content if b.type == "text"))
    plan["slot_type"] = plan.pop("kind")
    return plan


@app.get("/workout")
def workout(new: int = 0) -> dict:
    """Serve TODAY'S slot from the weekly program. Cardio and rest days are
    synthesized directly (no model call); lifting days get a detailed session
    with progressive overload from logged loads. new=1 swaps in a genuinely
    different session for today, whatever the slot type."""
    today = date.today().isoformat()
    if not new and today in workout_cache:
        return workout_cache[today]

    try:
        program = get_program()
    except Exception:
        return {"error": "Couldn't reach the programming coach — try again in a moment."}
    slot = next((d for d in program["days"] if d["day"] == date.today().strftime("%A")), None)
    slot_desc = f"{slot['type']} — {slot['title']}: {slot['detail']}" if slot else "full-body session"

    if new:
        seen = workout_seen.setdefault(today, [slot["title"]] if slot else [])
        try:
            plan = write_session(
                slot_desc,
                "He wants a DIFFERENT session today. Already shown and passed on: "
                f"{'; '.join(seen) or 'the programmed slot'}. Write a workout of a clearly "
                "different type than all of those that still serves this week's program. "
                "If the slot is rest or recovery, keep it moderate: a lighter lift for "
                "muscles not trained in 48 hours, a different cardio modality, or mobility work.",
            )
        except Exception:
            return {"error": "Couldn't reach the programming coach — try again in a moment."}
    elif slot and slot["type"] == "rest":
        plan = {
            "title": slot["title"] or "Rest & Recovery",
            "focus": slot["detail"],
            "duration_minutes": slot["duration_minutes"],
            "slot_type": "rest",
            "exercises": [{
                "exercise": "Easy movement", "sets": 1,
                "reps": f"{program['step_target']} steps",
                "rest_seconds": 0,
                "notes": "Recovery day — movement, not training. Steps still count toward the goal.",
                "video_query": "",
            }],
        }
    elif slot and slot["type"] in ("cardio", "hiit"):
        plan = {
            "title": slot["title"],
            "focus": f"{slot['detail']} — {program['week_focus']}",
            "duration_minutes": slot["duration_minutes"] + 5,   # + warm-up
            "slot_type": slot["type"],
            "exercises": [
                {"exercise": "Warm-up", "sets": 1, "reps": "5 min",
                 "rest_seconds": 0, "notes": "easy pace, RPE 3 — grease the hinges",
                 "video_query": ""},
                {"exercise": slot["title"], "sets": 1,
                 "reps": f"{slot['duration_minutes']} min",
                 "rest_seconds": 0, "notes": slot["detail"],
                 "video_query": slot.get("video_query") or f"{slot['title']} technique"},
            ],
        }
    else:  # lifting day (or no slot found — default to a lift)
        try:
            plan = write_session(slot_desc, "Write the lifting session.")
        except Exception:
            return {"error": "Couldn't reach the programming coach — try again in a moment."}

    plan["date"] = today   # lets an open page notice midnight and reset
    workout_cache[today] = plan
    workout_seen.setdefault(today, []).append(plan["title"])
    return plan


# ── Stakes: what finishing today is worth, shown BEFORE you start ─────────────
# A preview computed from the same rules that pay out. Read-only: showing the
# stakes never mints anything (rule #1) — only /workout/complete logs.

WORKOUT_UNLOCKS = [(1, "💪 First Rep trophy"), (10, "🏋️ Dumbbell gear"), (25, "🏗️ Quarter Hundred trophy")]
WORKOUT_BONUS_REASONS = {"quest:train", "quest_sweep", "chest:workout"}


def workout_logged_today(game: dict):
    """Title of today's workout if one is logged — from the Workout tab or from chat."""
    today = date.today().isoformat()
    if game["workout_completed"].get(today):
        return game["workout_completed"][today]
    for line in (WORKOUTS.read_text().splitlines() if WORKOUTS.exists() else []):
        if today in line:
            return line.split("—", 1)[-1].strip()[:80] or "Workout"
    return None


@app.get("/workout/stakes")
def workout_stakes() -> dict:
    today = date.today().isoformat()
    with game_lock:
        game = load_game()
    xp = xp_summary(game)
    fresh_day = game["quests"]["date"] != today
    quests = build_quests() if fresh_day else game["quests"]["items"]
    open_ids = {q["id"] for q in quests if not q["done"] and not quest_satisfied(q["id"])}
    quest_xp = 15 if "train" in open_ids else 0
    sweep_claimed = False if fresh_day else game["quests"]["sweep_claimed"]
    sweep_xp = 20 if open_ids == {"train"} and not sweep_claimed else 0
    gain = XP_RULES["workout"] + quest_xp + sweep_xp

    before = level_from_xp(xp["total"])
    after = level_from_xp(xp["total"] + gain)
    streak_now = streak_info()["streak"]
    streak_after = streak_info(frozenset({today}))["streak"]
    n = xp["counts"]["workout"]
    unlock = next(((need - n, name) for need, name in WORKOUT_UNLOCKS if need > n), None)
    earned = XP_RULES["workout"] + sum(
        b["amount"] for b in game["bonus_xp"] if b["date"] == today and b["reason"] in WORKOUT_BONUS_REASONS)
    return {
        "today": today,
        "done_today": workout_logged_today(game),
        "earned_today": earned,
        "xp": XP_RULES["workout"], "quest_xp": quest_xp, "sweep_xp": sweep_xp,
        "chest_pct": int(CHEST_CHANCE * 100),
        "level": {"n": before["n"], "pct": before["pct"]},
        "level_after": {"n": after["n"], "title": after["title"], "pct": after["pct"]},
        "streak": {"now": streak_now, "after": streak_after},
        "unlock": {"to_go": unlock[0], "name": unlock[1]} if unlock else None,
    }


class WorkoutDone(BaseModel):
    title: str = Field(max_length=200)
    details: str = Field(max_length=2000)


@app.post("/workout/complete")
def workout_complete(done: WorkoutDone) -> dict:
    today = date.today().isoformat()
    with game_lock:
        game = load_game()
        if workout_logged_today(game):
            return {"ok": True, "already": True, "events": []}   # no double-logging, even via chat
        events = refresh_quests(game)   # today's "train" quest must exist before the log lands
        result = execute_tool("log_workout", {"activity": done.title, "details": done.details})
        game["workout_completed"][today] = done.title
        events += refresh_quests(game) + maybe_chest(game, "workout") + collect_announcements(game)
        save_game(game)
    return {"ok": True, "already": False, "result": result, "events": events}


# ── Form videos: real IDs via YouTube Data API v3, quota-safe cache ───────────
# Each unique query costs API quota exactly once, ever — after that it's a
# local file read. No key → the frontend falls back to a search link.

VIDEO_CACHE_FILE = DATA / "video_cache.json"


# Ranking: YouTube's top hit is often a Short (vertical, no teaching) or a
# podcast clip. We pull 15 candidates, keep 1–15 min videos, and prefer titles
# that name the exercise AND read like instruction. The ranked list is cached,
# so "try another" walks it for free.
VIDEO_MIN_S, VIDEO_MAX_S = 60, 15 * 60
FORM_WORDS = {"form", "how", "technique", "tutorial", "guide", "proper", "properly",
              "correctly", "mistakes", "tips", "explained"}
JUNK_WORDS = ("podcast", "clips", "episode", "reaction", "vlog", "i tried", "#shorts",
              "full workout", "follow along")
STOP_WORDS = {"the", "a", "an", "to", "for", "and", "of", "with", "on", "in", "do", "your", "vs"}


def iso_seconds(iso: str) -> int:
    m = re.fullmatch(r"P(?:\d+D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    return 0 if not m else int(m[1] or 0) * 3600 + int(m[2] or 0) * 60 + int(m[3] or 0)


def rank_videos(query: str, items: list) -> list:
    words = [w for w in re.findall(r"[a-z0-9]+", query.lower())
             if w not in STOP_WORDS and w not in FORM_WORDS]
    scored = []
    for pos, it in enumerate(items):
        if not VIDEO_MIN_S <= iso_seconds(it["contentDetails"]["duration"]) <= VIDEO_MAX_S:
            continue
        title, channel = it["snippet"]["title"], it["snippet"]["channelTitle"]
        title_words = re.findall(r"[a-z0-9]+", title.lower())
        channel_words = re.findall(r"[a-z0-9]+", channel.lower())
        in_title = [w for w in words if any(h.startswith(w) for h in title_words)]
        in_channel = [w for w in words if w not in in_title and any(h.startswith(w) for h in channel_words)]
        # The exercise must be in the TITLE; a coach-name match on the channel is
        # only a nudge (else any Jeff Nippard video beats a real pulldown tutorial).
        score = 3 * len(in_title) / max(len(words), 1) + 0.3 * len(in_channel)
        score += 1.5 if FORM_WORDS & set(title_words) else 0
        score -= 3 if any(j in f"{title} {channel}".lower() for j in JUNK_WORDS) else 0
        score -= 0.1 * pos                                         # YouTube's order breaks ties
        scored.append((score, {"id": it["id"], "title": title, "channel": channel}))
    return [v for _, v in sorted(scored, key=lambda s: -s[0])]


def video_payload(entry: dict, cached: bool) -> dict:
    v = entry["videos"][entry["i"]]
    return {**v, "embed": f"https://www.youtube.com/embed/{v['id']}", "cached": cached,
            "n": entry["i"] + 1, "of": len(entry["videos"])}


@app.get("/video")
def video(q: str = "", another: int = 0) -> dict:
    key_q = q.strip().lower()
    if not key_q or len(key_q) > 200:
        return {"error": "no_query"}

    cache = {}
    if VIDEO_CACHE_FILE.exists():
        try:
            cache = json.loads(VIDEO_CACHE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            cache = {}
    entry = cache.get(key_q)
    if isinstance(entry, dict) and entry.get("videos"):   # old {q: id} entries get re-ranked
        if another:
            entry["i"] = (entry["i"] + 1) % len(entry["videos"])   # the reject goes to the back
            VIDEO_CACHE_FILE.write_text(json.dumps(cache, indent=2))
        return video_payload(entry, cached=True)

    key = os.environ.get("YOUTUBE_API_KEY")
    if not key:
        return {"error": "no_key"}

    try:
        auth = {"x-goog-api-key": key}   # header, not ?key= — URLs end up in error logs
        r = httpx.get("https://www.googleapis.com/youtube/v3/search", timeout=10, headers=auth, params={
            "part": "snippet", "q": q, "type": "video", "maxResults": 15, "videoEmbeddable": "true",
        })
        r.raise_for_status()
        ids = [i["id"]["videoId"] for i in r.json().get("items", [])]
        if not ids:
            return {"error": "no_results"}
        # 1 quota unit (vs 100 for the search) to get durations for ranking
        d = httpx.get("https://www.googleapis.com/youtube/v3/videos", timeout=10, headers=auth, params={
            "part": "contentDetails,snippet", "id": ",".join(ids),
        })
        d.raise_for_status()
        order = {vid: n for n, vid in enumerate(ids)}
        items = sorted(d.json().get("items", []), key=lambda it: order.get(it["id"], 99))
        videos = rank_videos(q, items)
        if not videos:
            return {"error": "no_results"}
        cache[key_q] = {"videos": videos, "i": 0}
        DATA.mkdir(parents=True, exist_ok=True)
        VIDEO_CACHE_FILE.write_text(json.dumps(cache, indent=2))
        return video_payload(cache[key_q], cached=False)
    except httpx.HTTPStatusError as e:
        # Log the real cause (a silent fallback hid a bad key for a month) —
        # status + Google's reason only, never the request itself.
        reason = e.response.text[:200].replace("\n", " ")
        print(f"[video] YouTube returned {e.response.status_code} for {q!r}: {reason}")
        return {"error": "api_error"}
    except Exception as e:
        print(f"[video] YouTube lookup failed for {q!r}: {type(e).__name__}")
        return {"error": "api_error"}


# ── PWA plumbing ──────────────────────────────────────────────────────────────

@app.get("/manifest.json")
def manifest() -> FileResponse:
    return FileResponse(HERE / "manifest.json", media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker() -> FileResponse:
    return FileResponse(HERE / "sw.js", media_type="application/javascript")


@app.get("/icon.svg")
def icon() -> FileResponse:
    return FileResponse(HERE / "icon.svg", media_type="image/svg+xml")


@app.get("/{name}.png")
def icon_png(name: str):
    # iOS home screens ignore SVG icons; these PNGs are what an installed app shows
    if name not in ("icon-192", "icon-512", "apple-touch-icon"):
        return JSONResponse({"error": "not_found"}, status_code=404)
    return FileResponse(HERE / "icons" / f"{name}.png", media_type="image/png")
