# Design notes: how the coach works and why

The coach is a chat agent with seven tools, a weekly program and a daily workout card, all built on the same Claude model. This file covers the choices behind it and the numbers that drove them. Most of the numbers come from `simulate.py`, which plays ten days of a fake user against the real app.

## 1. The agent loop

One coach turn: send the conversation and the tools, run any tool the model asks for, send the results back, and repeat until it answers in text (at most 8 rounds). Logging only happens through tools: `log_workout`, `log_meal`, `log_weight`, `save_profile`, `save_note`, `save_plan`, `mark_plan_kept`. If the model says "Logged" without calling one, nothing is saved, which is the worst failure the coach can have.

Three guards around the loop. The last two were added after a run showed the failure:

- **Roll back a failed turn.** A turn that errors halfway would leave a tool call without its result in the history, and every later request would be refused. The turn's messages are removed before the error is returned.
- **Log the error and recover from a refused conversation.** Run 14 lost most of day 1: after one reply, every chat turn failed within a second until the session reset, while the weekly program call in between worked. The app swallowed the error, so the cause is unknown. Now every failed call writes its type, status and message to stderr and `data/usage.jsonl`. When the API refuses a turn with earlier messages in the session and nothing saved yet, the app saves the session to the log and retries once from a fresh start.
- **Catch "Logged" with no tool call.** In run 13 the coach replied "Logged: 3 slices pepperoni + a Monster" and called nothing. When a sentence opens with "Logged" and no tool ran that turn, the loop sends one hidden check asking for the call. On the 297 turns of runs 1 to 13, that rule would have fired 3 times: the real miss, plus 2 recaps of data already saved ("Logged 3 days out of the last 7", "yesterday: logged the lift, marked it kept"), where the check tells it to save nothing.

## 2. What the model sees

The system prompt is three blocks, ordered from frozen to volatile, with a cache breakpoint after the first two:

1. The coaching instructions. The same bytes on every call, not even today's date.
2. The profile and recent session logs. Stable within a session.
3. Today: the date, the if-then plan, today's workout card, and the training, lift, weight and food logs.

A change in one block only re-bills the blocks after it. Run 1 had no caching and billed 330,704 input tokens at full price for $2.07. Run 16 billed 13,248 at full price, wrote 109,079 to the cache and read 305,847 from it at a tenth of the price, for $1.35.

The facts are computed in code, not left to the model:

- **Dates carry their weekday and age** ("Wed 2026-09-16 (7 days ago)"). Without them, run 1 called 8 days "three weeks".
- **Weight change and weekly averages are computed**, with a note when it's too early to call a trend.
- **Lift history covers every logged session**, not just the last 10 workouts, so the start is never lost. It reads the way a real log is written ("Bench 3x8 @140 lb"). The first version only read the simulator's format ("155x5x3") and found nothing in a real log.
- **Protein is summed by day** against the 175 g target.
- **Empty sections say so.** With no weigh-ins, the weight section used to be missing, and the coach decided it couldn't set a protein target without one. It asked for bodyweight in 4 to 5 replies in runs 8, 9 and 11. Now the section says there are no weigh-ins yet and nothing waits on one: 0 asks in runs 12, 13, 15 and 16.

Two rules about the saved session logs that feed block 2:

- **No tool calls in them.** They used to read "[log_workout] Logged…". Reading them back, the coach typed the marker instead of calling the tool (run 5).
- **They say when he didn't reply.** A proposal left hanging read as agreed the next day ("we locked yogurt + banana").

## 3. The simulator

`simulate.py` plays a fake user against the real app through its HTTP routes: 21, 5'10", about 184 lb, wants to lose 10 lb and bench 185, with left-knee patellar tendinitis that lunges and split squats flare. Ten days, 27 messages, plus the weekly program and workout cards.

- **Fake clock.** Day 8 really is "next Monday" to the app, so the week-2 program has a real week 1 behind it.
- **Fake data only.** The run writes to its own `runs/sim-<stamp>/data` folder, and it refuses to start if the app points anywhere else.
- **Hard budget.** Before each call, the meter checks whether the biggest call so far could push spending over the budget, and stops the run if it could.
- **A control user.** The same ten days with two messages changed: day 1 says "no injuries", and day 3's lunge set has no knee ache. Any difference from the knee runs comes from the injury.

Each run saves `transcript.md` (every message, tool call and reply) and `trace.json` (every API call with its tokens and cost, and the scorecard).

## 4. The scorecard

13 checks, written as string rules instead of a model acting as judge, because a judge's verdict can change when nothing changed. The rules are free, give the same answer every time, and print what they saw so I can check them against the transcript. Re-score any past run with `--score runs/<run>`.

- Every workout, meal and weigh-in he reports gets saved, and no tool call is typed as text.
- The intake is complete, and the if-then plan is saved and marked kept.
- Day 10's "how am I doing" uses the real numbers: weight from first to latest, first and latest bench, and the best protein day against 175 g.
- Day 8's chat gives the same weights as the workout card.
- No invented agreements, and no "three weeks" for a 10-day log.
- Skipped questions are let go: no counting asks ("third ask"), no "still need…" in the same session, and a number he hasn't given is asked for in at most 2 replies.
- Knee user: never suggests lunges or split squats (in chat, cards or the program), and doesn't nag about the knee.
- Control user: never invents an injury, and asks about injuries at most twice after "none".

The rules have been wrong too. Checking every flag against a hand reading found false positives: a sentence ruling out "split squats, lunges to a box, same tendon" read as a suggestion, and a warm-up ramp set read as a working weight. Both were fixed, and re-scoring every past run changed no other verdict.

## 5. Every run

Scores use today's 13-check scorecard, so the rows compare like for like. Runs 4, 8, 9 and 11 scored 12/12 on the 12-check card of the time; the skipped-question check was added after run 11.

| Run | User | Change before the run | Score | Cost | What it showed |
|---|---|---|---|---|---|
| 1 | knee | first version | 7/13 | $2.07 | chat contradicted the card, "three weeks" for 8 days, invented agreements |
| 2 | knee | dated context, caching, today's card in chat | 10/13 | $1.34 | "we locked yogurt", nagging |
| 3 | knee | note when he didn't reply | 9/13 | $1.31 | intake stuck open, asked about the knee about 12 times |
| 4 | knee | notes section, intake nudge, injury rule | 12/13 | $1.43 | chased bodyweight |
| 5 | knee | same code | 10/13 | $1.28 | typed "[log_workout] Logged…", saved nothing |
| 6 | knee | tool markers out of saved logs | 11/13 | $1.48 | nagged about the knee |
| 7 | knee | superseded, stopped early | | about $0.50 | |
| 8 | knee | final prompt | 12/13 | $1.43 | chased bodyweight |
| 9 | knee | same code | 12/13 | $1.37 | chased bodyweight |
| 10 | other | a different no-injury user, stopped on day 2 | | $0.49 | replaced by the control |
| 11 | control | control user | 12/13 | $1.32 | yes to lunges, no invented injury; chased bodyweight |
| 12 | knee | let skipped questions go, "nothing yet" notes | 13/13 | $1.34 | clean |
| 13 | control | same code | 12/13 | $1.23 | said "Logged" with no tool call |
| 14 | knee | "Logged" check | 9/13 | $0.81 | most of day 1 refused; all 4 failures follow from it |
| 15 | control | same code | 13/13 | $1.28 | clean |
| 16 | knee | error logging and recovery | 13/13 | $1.35 | clean |

## Known issues

- **Some answers aren't saved on the turn.** In runs 15 and 16 the training-history answer ("lifted in high school…") wasn't saved when he gave it, and the day-2 check-in didn't mark the plan kept. Both were saved later, so the scorecard passes, but the tool check on those two messages failed.
- **The coach no longer asks for a weigh-in at all.** The context says to ask once. In runs 12, 13, 15 and 16 it waited until he reported one on day 2.
- **Two guards haven't fired in a live run yet.** The "Logged" check and the refused-conversation retry are proven by offline tests only.
- **Why run 14's requests were refused is unknown.** The error wasn't logged yet. The next time it happens, `usage.jsonl` will say.
- **One scripted user.** A simulator only tests what it types. The lift-history bug above passed every simulated run and failed on a real log.
- **The scorecard only knows failures a run has already shown.** A new kind of failure passes until someone reads a transcript and writes a check for it.
