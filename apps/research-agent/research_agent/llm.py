"""The only module that talks to the API. Every response passes through the
Ledger, so cost is measured per call and per stage, never guessed."""

import os
import threading
import time
from dataclasses import asdict, dataclass

import anthropic

from . import config


def load_dotenv() -> None:
    env = config.ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            key, _, value = line.strip().partition("=")
            if key and not key.startswith("#"):
                os.environ.setdefault(key, value.strip().strip("\"'"))


load_dotenv()
client = anthropic.Anthropic(max_retries=3)   # SDK retries 429/5xx/connection errors with backoff

# Opus requests opt into server-side refusal fallback: a (rare) safety decline is
# re-run on Anthropic's recommended model for that category instead of failing the run.
OPUS_FALLBACK = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}


class AgentError(RuntimeError):
    """A stage can't continue (refusal, truncated output, nothing parseable)."""


@dataclass
class CallRecord:
    stage: str
    model: str
    input_tokens: int
    output_tokens: int
    cache_read: int
    cache_write: int
    searches: int
    seconds: float
    cost: float


class Ledger:
    """Usage + dollars for every call. The cost table and the trace both read this."""

    def __init__(self) -> None:
        self.calls: list[CallRecord] = []
        self._lock = threading.Lock()   # research workers record from several threads

    def record(self, stage: str, response, seconds: float) -> CallRecord:
        u = response.usage
        price_in, price_out = config.PRICES.get(response.model, config.PRICES[config.ORCHESTRATOR_MODEL])
        cache_read = u.cache_read_input_tokens or 0
        cache_write = u.cache_creation_input_tokens or 0
        searches = getattr(getattr(u, "server_tool_use", None), "web_search_requests", 0) or 0
        cost = (
            u.input_tokens * price_in
            + cache_write * price_in * config.CACHE_WRITE_MULT
            + cache_read * price_in * config.CACHE_READ_MULT
            + u.output_tokens * price_out
        ) / 1e6 + searches * config.WEB_SEARCH_PRICE
        rec = CallRecord(stage, response.model, u.input_tokens, u.output_tokens,
                         cache_read, cache_write, searches, round(seconds, 2), cost)
        with self._lock:
            self.calls.append(rec)
        return rec

    def total(self) -> float:
        return sum(c.cost for c in self.calls)

    def by_stage(self) -> dict:
        out: dict = {}
        for c in self.calls:
            s = out.setdefault(c.stage, {"calls": 0, "searches": 0, "input_tokens": 0,
                                         "output_tokens": 0, "cost": 0.0})
            s["calls"] += 1
            s["searches"] += c.searches
            s["input_tokens"] += c.input_tokens + c.cache_read + c.cache_write
            s["output_tokens"] += c.output_tokens
            s["cost"] += c.cost
        return out

    def as_dicts(self) -> list:
        return [asdict(c) for c in self.calls]


def structured(ledger: Ledger, stage: str, *, model: str, system: str, content: str,
               schema, max_tokens: int = 16000, effort: str = "medium"):
    """One call whose reply is validated against a Pydantic schema. Returns the parsed object."""
    kwargs = dict(model=model, max_tokens=max_tokens, system=system, output_format=schema,
                  output_config={"effort": effort},
                  messages=[{"role": "user", "content": content}])
    if model == config.ORCHESTRATOR_MODEL:
        kwargs.update(OPUS_FALLBACK)
    start = time.time()
    response = client.beta.messages.parse(**kwargs)
    ledger.record(stage, response, time.time() - start)
    if response.stop_reason == "refusal":
        raise AgentError(f"{stage}: the model declined this request")
    if response.stop_reason == "max_tokens":
        raise AgentError(f"{stage}: output hit max_tokens ({max_tokens}) and was cut off")
    if response.parsed_output is None:
        raise AgentError(f"{stage}: no parseable output")
    return response.parsed_output


def web_search_turn(ledger: Ledger, stage: str, *, system: str, content: str, max_searches: int) -> tuple:
    """A worker turn with Claude's server-side web search. Returns (content blocks, stop_reason).

    Search runs on Anthropic's servers inside one API call. That server loop can stop
    early with stop_reason "pause_turn"; resuming means re-sending the conversation with
    the partial assistant turn appended (no extra "continue" message)."""
    # The basic search tool, on purpose. The newer web_search_20260209 adds "dynamic
    # filtering": the model searches from a code sandbox and reads filtered stdout, so the
    # answer arrives with NO citation objects (measured: 0 citations, 150 s per worker).
    # This agent is built on provenance, so citations beat the token savings (29 s, cited).
    tools = [{"type": "web_search_20250305", "name": "web_search", "max_uses": max_searches}]
    messages = [{"role": "user", "content": content}]
    blocks: list = []
    response = None
    for _ in range(config.MAX_PAUSE_CONTINUATIONS + 1):
        start = time.time()
        response = client.messages.create(
            model=config.WORKER_MODEL, max_tokens=8000, system=system, tools=tools,
            messages=messages, output_config={"effort": "medium"},
        )
        ledger.record(stage, response, time.time() - start)
        blocks.extend(response.content)
        if response.stop_reason != "pause_turn":
            break
        messages = messages[:1] + [{"role": "assistant", "content": list(blocks)}]   # everything so far
    return blocks, response.stop_reason
