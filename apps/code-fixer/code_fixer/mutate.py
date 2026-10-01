"""Planted bugs, generated: one-token slips in real code, for eval cases.

Each mutant is a slip a person makes: a boundary off by one (< for <=), a flipped or negated
comparison, `or` for `and`, a dropped `not`, - for +, an integer off by one, or a missing
statement (a state update nobody made). A mutant is a text edit in the case format
({"file", "find", "replace"}, `find` unique in the file), so a generated bug is planted exactly
like a hand-made one. Whether a mutant makes a good case is decided in the sandbox
(mutants.py), not here."""

import ast
import bisect
import io
import random
import re
import tokenize
from dataclasses import asdict, dataclass

COMPARE_TEXT = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=", ast.Eq: "==", ast.NotEq: "!=",
                ast.Is: "is", ast.IsNot: "is not", ast.In: "in", ast.NotIn: "not in"}
COMPARE_SWAPS = {"<": [("<=", "boundary"), (">", "flip")], "<=": [("<", "boundary"), (">=", "flip")],
                 ">": [(">=", "boundary"), ("<", "flip")], ">=": [(">", "boundary"), ("<=", "flip")],
                 "==": [("!=", "negate")], "!=": [("==", "negate")], "is": [("is not", "negate")],
                 "is not": [("is", "negate")], "in": [("not in", "negate")], "not in": [("in", "negate")]}
ARITH = {ast.Add: ("+", "-"), ast.Sub: ("-", "+")}
AUG = {ast.Add: ("+=", "-="), ast.Sub: ("-=", "+=")}
BOOL = {ast.And: ("and", "or"), ast.Or: ("or", "and")}
SMALL_INT = 10          # integers mutated by one either way: 0 <= n <= this
DELETABLE = (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Expr, ast.Delete)
SKIP_TOKENS = {tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT, tokenize.INDENT, tokenize.DEDENT}
CATEGORY = {"boundary": "off-by-one", "const": "off-by-one", "flip": "flipped comparison",
            "negate": "flipped comparison", "bool": "wrong operator", "arith": "wrong operator",
            "not": "missed negation", "delete": "missing statement"}

# A screened mutant's visible failures: "E   NameError: …" in a traceback, or the summary's
# "FAILED tests/x.py::t - NameError: …" (pytest cuts summary lines at 80 columns, names too).
ERROR_LINE = re.compile(r"^E\s+(\w+(?:Error|Exception|Exit))\b")
FAILURE_LINE = re.compile(r"^(?:FAILED|ERROR) \S+ - (\w+(?:Error|Exception|Exit))\b")
# Errors that name their own cause: the message is the answer. A deleted `k = key(...)` fails
# with "name 'k' is not defined"; a deleted `self.match = match` with "has no attribute 'match'".
SELF_ANNOUNCING = {"NameError", "UnboundLocalError"}
SELF_ANNOUNCING_DELETE = SELF_ANNOUNCING | {"AttributeError"}
# A constant slip that reads as a no-op (`misses += 0`, `x * 1`): no person writes it, and it gives itself away.
NO_OP = re.compile(r"(?:[-+]=?\s*0|[*/]=?\s*1)(?![\w.])")


def errors_in(output: str) -> list:
    found = set()
    for line in output.splitlines():
        m = ERROR_LINE.match(line) or FAILURE_LINE.match(line)
        if m:
            found.add(m[1])
    return sorted(found)


@dataclass
class Mutant:
    file: str          # relative to the source project
    line: int          # 1-based line of the change in the original file
    function: str      # Class.method around it ("" at module level)
    kind: str          # boundary, flip, negate, bool, not, arith, const, delete
    before: str        # the changed line(s) as they were, for reading
    after: str         # and with the mutant ("" when the statement is gone)
    find: str          # the case edit: occurs exactly once in the file
    replace: str

    @property
    def category(self) -> str:
        return CATEGORY[self.kind]

    @property
    def edit(self) -> dict:
        return {"file": self.file, "find": self.find, "replace": self.replace}

    def as_dict(self) -> dict:
        return {**asdict(self), "category": self.category}


class _Source:
    """Offsets into one file's text: AST positions (UTF-8 byte columns) and tokens (characters)."""

    def __init__(self, text: str):
        self.text = text
        self.lines = text.splitlines(keepends=True)
        self.starts = [0]
        for line in self.lines:
            self.starts.append(self.starts[-1] + len(line))
        self.tokens = []
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type not in SKIP_TOKENS and tok.type != tokenize.ENDMARKER:
                self.tokens.append((self.at(*tok.start), self.at(*tok.end), tok.string))
        self.token_starts = [t[0] for t in self.tokens]

    def at(self, row: int, col: int) -> int:
        return self.starts[row - 1] + col

    def node_start(self, node) -> int:
        line = self.lines[node.lineno - 1]
        return self.starts[node.lineno - 1] + len(line.encode()[:node.col_offset].decode(errors="ignore"))

    def node_end(self, node) -> int:
        line = self.lines[node.end_lineno - 1]
        return self.starts[node.end_lineno - 1] + len(line.encode()[:node.end_col_offset].decode(errors="ignore"))

    def tokens_between(self, start: int, end: int) -> list:
        i = bisect.bisect_left(self.token_starts, start)
        found = []
        while i < len(self.tokens) and self.tokens[i][1] <= end:
            found.append(self.tokens[i])
            i += 1
        return found

    def operator_between(self, left, right, expected: str):
        """The (start, end) of operator `expected` between two nodes, ignoring parentheses; None if
        anything else is there."""
        toks = [t for t in self.tokens_between(self.node_end(left), self.node_start(right)) if t[2] not in "()"]
        if " ".join(t[2] for t in toks) != expected:
            return None
        return toks[0][0], toks[-1][1]


def _walk(node, name="", in_function=False):
    yield node, name, in_function
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            inner = f"{name}.{child.name}" if name else child.name
            yield from _walk(child, inner, in_function or not isinstance(child, ast.ClassDef))
        else:
            yield from _walk(child, name, in_function)


def _changes(src: _Source, node, in_function: bool):
    """(start, end, new text, kind) for every mutation of this node."""
    if isinstance(node, ast.Compare):
        operands = [node.left] + node.comparators
        for i, op in enumerate(node.ops):
            text = COMPARE_TEXT[type(op)]
            span = src.operator_between(operands[i], operands[i + 1], text)
            if span:
                for new, kind in COMPARE_SWAPS[text]:
                    yield span[0], span[1], new, kind
    elif isinstance(node, ast.BinOp) and type(node.op) in ARITH:
        if not any(isinstance(side, ast.JoinedStr) or (isinstance(side, ast.Constant) and isinstance(side.value, str))
                   for side in (node.left, node.right)):
            old, new = ARITH[type(node.op)]
            span = src.operator_between(node.left, node.right, old)
            if span:
                yield span[0], span[1], new, "arith"
    elif isinstance(node, ast.AugAssign) and type(node.op) in AUG:
        old, new = AUG[type(node.op)]
        span = src.operator_between(node.target, node.value, old)
        if span:
            yield span[0], span[1], new, "arith"
    elif isinstance(node, ast.BoolOp) and len(node.values) == 2:
        old, new = BOOL[type(node.op)]
        span = src.operator_between(node.values[0], node.values[1], old)
        if span:
            yield span[0], span[1], new, "bool"
    elif isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        toks = src.tokens_between(src.node_start(node), src.node_end(node))
        if len(toks) >= 2 and toks[0][2] == "not":
            yield toks[0][0], toks[1][0], "", "not"
    elif isinstance(node, ast.Constant) and type(node.value) is int and 0 <= node.value <= SMALL_INT:
        start, end = src.node_start(node), src.node_end(node)
        if src.text[start:end] == str(node.value):
            for new in [node.value + 1] + ([node.value - 1] if node.value >= 1 else []):
                yield start, end, str(new), "const"
    if in_function:
        for field in ("body", "orelse", "finalbody"):
            block = getattr(node, field, None)
            if isinstance(block, list) and len(block) >= 2:
                for stmt in block:
                    span = _deletable(src, stmt)
                    if span:
                        yield span[0], span[1], "", "delete"


def _deletable(src: _Source, stmt):
    """The whole lines of a state update or call statement, or None."""
    if not isinstance(stmt, DELETABLE):
        return None
    if isinstance(stmt, ast.Expr) and not isinstance(stmt.value, ast.Call):
        return None   # docstrings and bare names
    if isinstance(stmt, ast.AnnAssign) and stmt.value is None:
        return None
    first, last = src.lines[stmt.lineno - 1], src.lines[stmt.end_lineno - 1]
    start, end = src.node_start(stmt), src.node_end(stmt)
    if first[:start - src.starts[stmt.lineno - 1]].strip():
        return None   # something before it on the line
    rest = src.text[end:src.starts[stmt.end_lineno - 1] + len(last)].strip()
    if rest and not rest.startswith("#"):
        return None   # another statement after it (a ";")
    return src.starts[stmt.lineno - 1], src.starts[stmt.end_lineno]


def _only_at(text: str, lo: int, hi: int) -> bool:
    """text[lo:hi] occurs nowhere else, overlapping copies included (str.count misses those)."""
    chunk = text[lo:hi]
    return text.find(chunk) == lo and text.find(chunk, lo + 1) == -1


def _edit(text: str, start: int, end: int, new: str) -> tuple:
    """The smallest run of whole lines around the change whose text occurs once in the file:
    (find, replace, before, after, first line)."""
    lo = text.rfind("\n", 0, start) + 1
    if end > start and text[end - 1] == "\n":   # a deletion of whole lines
        hi = end
    else:
        hi = text.find("\n", end) + 1 or len(text)
    before, after = text[lo:hi].strip(), (text[lo:start] + new + text[end:hi]).strip()
    line = text.count("\n", 0, start) + 1
    grow_before = True
    while not _only_at(text, lo, hi):
        if (grow_before and lo > 0) or hi >= len(text):
            lo = text.rfind("\n", 0, lo - 1) + 1
        else:
            hi = text.find("\n", hi) + 1 or len(text)
        grow_before = not grow_before
    return text[lo:hi], text[lo:start] + new + text[end:hi], before, after, line


def mutants(text: str, file: str) -> list:
    """Every mutant of one Python file that parses and changes the program."""
    src = _Source(text)
    original = ast.dump(ast.parse(text))
    found, seen = [], set()
    for node, function, in_function in _walk(ast.parse(text)):
        for start, end, new, kind in _changes(src, node, in_function):
            mutated = text[:start] + new + text[end:]
            try:
                if ast.dump(ast.parse(mutated)) == original:
                    continue
            except SyntaxError:
                continue
            find, replace, before, after, line = _edit(text, start, end, new)
            if (find, replace) in seen:
                continue
            seen.add((find, replace))
            found.append(Mutant(file, line, function, kind, before, after, find, replace))
    return sorted(found, key=lambda m: (m.line, m.kind, m.replace))


def eligible(rows: list) -> list:
    """Caught by a visible test, and by a hidden one too (so a partial fix is caught)."""
    return [r for r in rows if r["status"] == "caught" and r["visible_failing"] >= 1 and r["hidden_failing"] >= 1]


def pick(rows: list, count: int, seed: int) -> list:
    """One mutant per function, varied by kind. Left out: a bug whose traceback points at its own
    line, whose error names its cause (SELF_ANNOUNCING), or that reads as a no-op (NO_OP). For
    each kind and function, the weakest visible signal (fewest failing visible tests); then the
    kinds take turns, ties broken by the seed."""
    rng = random.Random(seed)
    usable = [r for r in eligible(rows) if not r["shows_line"]
              and not (SELF_ANNOUNCING_DELETE if r["kind"] == "delete" else SELF_ANNOUNCING) & set(r.get("errors", []))
              and not (r["kind"] == "const" and NO_OP.search(r["after"]) and not NO_OP.search(r["before"]))]
    by_kind = {}
    for r in sorted(usable, key=lambda r: (r["visible_failing"], rng.random())):
        by_kind.setdefault(r["kind"], {}).setdefault((r["file"], r["function"]), r)
    queues = {kind: rng.sample(list(reps.values()), len(reps)) for kind, reps in sorted(by_kind.items())}
    chosen, used = [], set()
    while len(chosen) < count and any(queues.values()):
        for kind in sorted(queues):
            while queues[kind] and len(chosen) < count:
                r = queues[kind].pop()
                if (r["file"], r["function"]) not in used:
                    used.add((r["file"], r["function"]))
                    chosen.append(r)
                    break
    return chosen
