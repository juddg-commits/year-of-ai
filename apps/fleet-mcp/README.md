# Fleet MCP server: every agent, one plug

My agents are standalone apps, each with its own folder, dependencies, tests and ship. This small [MCP](https://modelcontextprotocol.io) server exposes all of them as tools, so an orchestrator (my Mother agent in Claude Code, or any MCP client) can call them.

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
- **Agents never share dependencies.** A new agent is one more tool function here, nothing else changes.
- **Progress streams to the client.** The agent's `[3/6]` stage lines become MCP progress notifications.
- **Money is guarded.** The server caps the settings a caller can ask for (4 sub-questions × 3 searches; $0.50 a fix). A cancelled or stuck call kills the agent process, so it stops spending. Every failure comes back as a tool error that says what was spent.
- **Big results don't overflow the caller.** Claude Code caps a tool result at ~25k tokens, so long output is cut and points to the saved file.
- **stdout is off limits.** It carries the MCP protocol itself, so the server never prints to it.

## Run it

Needs Python 3.10+. The agents it calls need their own setup (see each README).

```bash
cd apps/fleet-mcp
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover tests      # offline tests with fake agents, free
```

Claude Code starts it from `.mcp.json` at the repo root and asks once whether to trust it. Check with `claude mcp list`.
