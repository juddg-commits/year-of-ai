# Fleet MCP server: every agent, one plug

My agents are standalone apps, each with its own folder, dependencies, tests and ship. This small [MCP](https://modelcontextprotocol.io) server exposes all of them as tools, so an orchestrator (my Mother agent in Claude Code, or any MCP client) can call them.

**There's no result for the server itself yet.** Its offline tests use fake agents, and the costs below come from the agents' own saved runs. It runs locally over stdio for one user. The only thing that calls it so far is my own orchestrator (a Claude Code subagent whose definition isn't in this repo), and I haven't published it to the MCP registry.

| Tool | What it does | Cost |
|---|---|---|
| `research(question, sub_questions=4, searches=3)` | Runs the [research agent](../research-agent/): a brief where every sentence cites its source | $1.25 and 2.5 min at 4 × 3 on the tuned run (runs/20260928-092603, before quote recovery); $0.37 and 7.5 min at 2 × 1 on the shipped code (runs/20260928-102158) |
| `recent_research(limit=10)` | Lists past briefs and their files, so a caller can reuse one instead of paying again | free |
| `fix_code(repo_path, test_command="python -m pytest -q", max_usd=0.50)` | Runs the [code fixer](../code-fixer/): a Python repo with failing tests in, a patch checked in a fresh sandbox out. It never edits the repo | ~$0.06 a fix on the dev eval; the server caps it at $0.50 |

## How it works

Every agent follows one **result contract**: run its CLI with `--json`, and it prints progress on stderr and exactly one JSON object on stdout:

```json
{"agent": "research-agent", "status": "ok", "output": "# Brief…", "error": null,
 "cost_usd": 0.22, "seconds": 73.4, "artifacts": {"brief": "…/runs/x.md", "trace": "…/runs/x.json"}}
```

The server runs that CLI in the agent's own venv and relays the result. So:
- **Agents never share dependencies.** A new agent is one more tool function here, plus its name in the orchestrator's tool list, because Claude Code won't take a wildcard there. None of the other agents change.
- **Progress streams to the client.** The agent's `[3/6]` stage lines become MCP progress notifications.
- **Money is guarded.** The server caps the settings a caller can ask for (4 sub-questions × 3 searches; $0.50 a fix). A cancelled or stuck call kills the agent process, so it stops spending. Every failure comes back as a tool error, and when the agent reports the failure itself, the error says what it spent.
- **Big results don't overflow the caller.** Claude Code caps a tool result at ~25k tokens, so long output is cut and points to the saved file.
- **stdout is off limits.** It carries the MCP protocol itself, so the server never prints to it.

## Threat model

Mother, my orchestrator, is the part of the fleet to worry about. It has all three legs of what Simon Willison calls [the lethal trifecta](https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/): private data, untrusted content, and a way to send data out. The server itself holds no data and has no powers beyond the agents', so the risk sits with whatever calls it, and today that's Mother.

It can read files on my machine, including its notes and the agents' `.env` files with their API keys. It reads untrusted content, because the briefs these tools return are built from web pages, and it can search and fetch the web on its own. And it has a way out, since a web fetch or a search can carry data in its URL or query.

Right now, Claude Code's permissions are what hold that in check. `research` and `fix_code` aren't on my pre-approved list, so in the default mode I'm asked before every paid call, and since 2026-10-05 web search and fetch ask first too. That narrows the way out without closing it. Mother can still hand work to other agents, and in my setup some of their tools can reach the network without asking.

Whatever the caller asks for, the server keeps its own limits: 4 sub-questions × 3 searches, $0.50 a fix, and 15 minutes for a brief or 20 for a fix, after which the agent's process is killed, the same as when a call is cancelled. Results get cut at 40,000 characters and point to the saved file, and `fix_code` only takes an absolute path to an existing folder.

There's no injection test for the fleet, so its catch rate is not measured. Text the agents return can carry instructions from the pages they read, and the server passes it through unchanged apart from that cut.

## Run it

Needs Python 3.10+. The agents it calls need their own setup (see each README).

```bash
cd apps/fleet-mcp
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover tests      # offline tests with fake agents, free
```

Claude Code starts it from `.mcp.json` at the repo root and asks once whether to trust it. Check with `claude mcp list`.
