# Ship #1: Coach, a personal trainer that takes real actions

This is project #1 of my Year of AI, where I'm spending a year building AI projects in public.

**What it is:** a personal trainer that remembers you between sessions and takes real actions. It logs workouts, meals and weigh-ins, saves if-then plans, and programs your week from the loads you actually logged. One Python engine file, a FastAPI server around it, and a phone-first web app as the face.

I built it with Claude Code as my pair programmer.

## How I tested it

I can't chat with it for ten days every time I change a line, so I wrote a simulator. It plays a made-up user for 10 days and 27 messages, then scores the run on 13 checks written as code. The first version scored 7 of 13. The last five runs scored 13, 12, 9, 13 and 13; the 9 was a run that lost most of its first day to refused API calls.

The first full run cost $2.07. Ordering the prompt from the part that never changes to the part that does, and caching the frozen part, brought the same run to $1.34, even though the same change gave the coach more context.

## What broke

**I typed a chat message into the source file.** A weigh-in meant for the coach went into coach.py instead. Lesson: the file is the kitchen, the chat is the dining room. The fix became the log_weight tool.

**The YouTube key was the wrong kind of credential.** I'd made it in Google AI Studio, which now issues `AQ.` keys. The YouTube Data API only takes classic `AIza` keys from Cloud Console, so the app quietly fell back to search links. Lesson: test an integration once with a real call, and log the real error instead of swallowing it.

**It said "Logged" and saved nothing.** In 3 of 297 simulated replies, the coach told the user something was logged with no tool call behind it. In one run it had picked that up from its own saved chats, where past tool calls showed up as text, so it typed the text instead of making the call. Saved chats no longer show tool calls, and code now catches a "Logged" with no tool call in the same turn and asks once for the call.

**It kept asking for a weigh-in.** With none on file, the coach asked for bodyweight in 4 to 5 replies per run. To the model, the empty section read like a blocker. Once that section said "nothing yet, and nothing waits on it," and a new rule told it to let skipped questions go, it asked zero times in the next four runs.

**I blew my own ship window.** I kept building new versions and didn't commit any of them until the end. Lesson: commit on day one. "Done" is on GitHub, not on my laptop.

## What I learned

- **The agent loop is small:** decide, call a tool, feed the result back, continue. Everything else is plumbing.
- **Structured outputs make programming quality come from context** (real logged loads, week position), not cleverness.
- **Give the model the data; don't make it remember or compute.** In the first simulated run, it called 8 days "three weeks." Every date it sees now carries its weekday and age, and trends are computed in code.
- **XP that can only be earned from real log files is the honest version of gamification.** A tap can't mint it.
- **Behavior science translates directly into product rules:** never miss twice, if-then plans, fresh starts.

## What's next

Two more projects have shipped since: a research agent that cites every sentence (ship #2) and a code fixer that has to prove its fix (ship #3).

Code: https://github.com/juddg-commits/year-of-ai/tree/main/apps/health-coach

Every number here comes from a saved simulator run (kept on my machine), and [DESIGN.md](DESIGN.md) names the run behind each one.
