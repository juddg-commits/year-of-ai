"""The only module that talks to the API. Every response goes through the Ledger, so cost is
measured per call, never guessed, and the dollar ceiling is applied before each call: the
call's output limit is cut to what the remaining budget can pay for."""

import os
from dataclasses import dataclass

from . import config

# Server-side refusal fallback (Opus models only): a classifier decline is re-run on the model
# Anthropic recommends for it, instead of ending the fix.
FALLBACK = {"betas": ["server-side-fallback-2026-07-01"], "fallbacks": "default"}


def load_dotenv() -> None:
    env = config.ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            key, _, value = line.strip().partition("=")
            if key and not key.startswith("#"):
                os.environ.setdefault(key, value.strip().strip("\"'"))


def make_client():
    import anthropic   # imported here so the offline tests never need the SDK's network setup
    load_dotenv()
    return anthropic.Anthropic(max_retries=config.API_RETRIES)


def prices(model: str) -> tuple:
    """(input, output, cache write, cache read) $/MTok. An unknown model is priced at the most
    expensive known row, so the ledger can overstate a cost but never understate it."""
    for key in sorted(config.PRICES, key=len, reverse=True):
        if model == key or model.startswith(key + "-"):
            return config.PRICES[key]
    return max(config.PRICES.values(), key=lambda row: row[1])


def cost_of(model: str, input_tokens: int, output_tokens: int, cache_read: int, cache_write: int) -> float:
    p_in, p_out, p_write, p_read = prices(model)
    return (input_tokens * p_in + output_tokens * p_out + cache_write * p_write + cache_read * p_read) / 1e6


@dataclass
class CallRecord:
    turn: int
    model: str              # the model that answered: a refused request can be served by a fallback
    input_tokens: int
    output_tokens: int
    cache_read: int
    cache_write: int
    seconds: float
    cost: float
    stop_reason: str
    max_tokens: int
    fallback: bool = False

    @property
    def prompt_tokens(self) -> int:
        return self.input_tokens + self.cache_read + self.cache_write


class Ledger:
    def __init__(self) -> None:
        self.calls: list[CallRecord] = []

    def record(self, turn: int, requested: str, response, seconds: float, max_tokens: int) -> CallRecord:
        """With fallbacks, usage.iterations lists every attempt (a declined one included), each billed
        at the rates of the model that ran it; the top-level usage covers only the last attempt."""
        u = response.usage
        iterations = [it for it in (getattr(u, "iterations", None) or [])
                      if getattr(it, "type", None) in ("message", "fallback_message")]
        attempts = [(it, requested if it.type == "message" else it.model) for it in iterations]
        if not attempts:   # no fallback in play: the top-level usage is the whole call
            attempts = [(u, response.model)]
        tokens = [0, 0, 0, 0]
        cost = 0.0
        for it, model in attempts:
            t = [it.input_tokens or 0, it.output_tokens or 0,
                 it.cache_read_input_tokens or 0, it.cache_creation_input_tokens or 0]
            tokens = [a + b for a, b in zip(tokens, t)]
            cost += cost_of(model, *t)
        rec = CallRecord(turn, response.model, *tokens, round(seconds, 2), cost, response.stop_reason or "",
                         max_tokens, fallback=any(it.type == "fallback_message" for it in iterations))
        self.calls.append(rec)
        return rec

    def total(self) -> float:
        return sum(c.cost for c in self.calls)


def affordable_max_tokens(model: str, remaining_usd: float, cached_tokens: int, new_tokens: int,
                          cache_hit: bool = True) -> int:
    """The most output the remaining budget can pay for once this call's input is paid: the part
    of the prompt the last call already sent (cache-read price, or full input price if caching
    didn't happen) plus the new part (cache-write price), with 15% slack on the estimate."""
    p_in, p_out, p_write, p_read = prices(model)
    input_cost = 1.15 * (cached_tokens * (p_read if cache_hit else p_in) + new_tokens * p_write) / 1e6
    return max(0, int((remaining_usd - input_cost) * 1e6 / p_out))


def estimate_tokens(chars: int) -> int:
    return chars // 3 + 1   # code runs about 3-4 characters per token; err high


def call(client, *, model: str, effort: str, system: list, tools: list, messages: list, max_tokens: int,
         timeout: float | None = None):
    """One model turn, streamed (long thinking can't time out the HTTP request) and returned whole.
    An explicit cache breakpoint sits on the system prompt; top-level automatic caching moves a
    second one along the growing conversation, so each turn re-reads the last from cache.

    `timeout` bounds each attempt: on a stream it's how long the API may send nothing (it sends
    pings while the model thinks, so only a stall trips it). A stalled call raises APITimeoutError."""
    if timeout is not None:
        client = client.with_options(timeout=timeout)
    kwargs = dict(model=model, max_tokens=max_tokens, system=system, tools=tools, messages=messages,
                  thinking={"type": "adaptive", "display": "summarized"},
                  output_config={"effort": effort}, cache_control={"type": "ephemeral"})
    if model in config.FALLBACK_MODELS:
        kwargs.update(FALLBACK)
    with client.beta.messages.stream(**kwargs) as stream:
        return stream.get_final_message()


def echo_content(content: list) -> list:
    """The assistant turn as it goes back into the conversation. After a mid-stream fallback, the
    declined model's thinking and tool calls before the last fallback boundary are left out
    (the API's rule for echoing fallback turns); everything else goes back unchanged."""
    boundary = max((i for i, b in enumerate(content) if b.type == "fallback"), default=None)
    if boundary is None:
        return list(content)
    drop = {"thinking", "redacted_thinking", "tool_use", "server_tool_use"}
    return [b for i, b in enumerate(content) if i >= boundary or b.type not in drop]
