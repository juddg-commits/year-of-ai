# Ship #1 write-up: Health Coach (draft, Judd edits before posting)

_What it is, one line:_ a personal trainer that remembers you between sessions and takes real actions (logs workouts, meals, weigh-ins, saves if-then plans) — one Python engine file, a FastAPI skin, a PWA face.

## What broke
- **I typed a chat message into the source file.** "i weigh 185" went into `coach.py` at line 103 instead of into the coach. Lesson: the file is the kitchen, the chat is the dining room. The fix became the `log_weight` tool.
- **The YouTube key was the wrong kind of credential.** I'd made it in Google AI Studio, which now issues `AQ.` keys; the YouTube Data API rejects those outright ("API keys are not supported by this API") and only takes classic `AIza` keys from Cloud Console. The app silently fell back to search links for a month. Lesson: test the integration once with a real call, not by eyeballing the UI, and log the real error instead of swallowing it.
- **Ship window blown.** Built to v8 in a week, then didn't push for a month. The repo had zero commits. Lesson: commit on day one; "done" is on GitHub, not on my laptop.

## What I learned
- The agent loop is small: decide → call tool → feed result back → continue. Everything else is plumbing.
- Structured outputs make programming quality come from *context* (real logged loads, week position), not cleverness.
- XP that can only be earned from real log files is the honest version of gamification; a tap can't mint it.
- Behavior science translates directly into product rules: never-miss-twice, if-then plans, fresh starts.

## What's next
- Use it daily for a week, then Project #2 (Research CLI with citations).
