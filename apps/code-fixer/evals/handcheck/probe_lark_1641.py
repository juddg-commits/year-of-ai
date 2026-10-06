"""lark-1641: a copy of a parse in progress, made with InteractiveParser.copy() or by copying the
parser state alone, must finish on its own and leave the original able to finish too."""
import json
import random
from copy import copy

from lark import Lark
from lark.parsers.lalr_interactive_parser import InteractiveParser

GRAMMAR = r"""
start: item+
item: NAME "=" NUMBER ";"
NAME: /[a-z]+/
NUMBER: /[0-9]+/
%ignore " "
"""


def outcome(fn):
    try:
        return ["ok", str(fn())]
    except Exception as e:
        return [type(e).__name__]


results = []
for lexer in ("basic", "contextual"):
    parser = Lark(GRAMMAR, parser="lalr", lexer=lexer)
    for seed in range(120):
        rng = random.Random(seed)
        text = " ".join(f"{''.join(rng.choices('abc', k=rng.randint(1, 3)))} = {rng.randint(0, 99)};"
                        for _ in range(rng.randint(1, 5)))
        n_tokens = 4 * text.count(";")
        k = rng.randint(0, n_tokens)
        ip = parser.parse_interactive(text)
        tokens = ip.lexer_thread.lex(ip.parser_state)
        for _ in range(k):
            ip.feed_token(next(tokens))
        how = rng.choice(["ip.copy", "state.copy", "copy(ip)"])
        if how == "ip.copy":
            twin = ip.copy()
        elif how == "state.copy":
            state = ip.parser_state.copy()
            twin = InteractiveParser(ip.parser, state, state.lexer)
        else:
            twin = copy(ip)
        first, second = (twin, ip) if rng.random() < 0.5 else (ip, twin)
        results.append([lexer, seed, k, how, outcome(first.resume_parse), outcome(second.resume_parse)])
print(json.dumps(results))
