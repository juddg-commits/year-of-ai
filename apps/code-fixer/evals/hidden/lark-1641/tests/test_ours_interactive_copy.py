"""Ours (a hidden test for the code fixer's eval, not upstream's): a copy of a parse in progress
must be independent of the original, whichever is finished first, from any point in the input,
and whichever way it was copied: InteractiveParser.copy(), copy() of the parser state alone, or
the immutable interactive parser."""
from copy import copy

import pytest

from lark import Lark

GRAMMAR = r"""
start: item+
item: NAME "=" NUMBER ";"
NAME: /[a-z]+/
NUMBER: /[0-9]+/
%ignore " "
"""
TEXT = "a = 1; bb = 22; c = 3;"
N_TOKENS = 12


@pytest.fixture(params=["basic", "contextual"])
def parser(request):
    return Lark(GRAMMAR, parser="lalr", lexer=request.param)


def advanced(parser, k):
    """An interactive parse of TEXT with its first k tokens lexed and fed."""
    ip = parser.parse_interactive(TEXT)
    tokens = ip.lexer_thread.lex(ip.parser_state)
    for _ in range(k):
        ip.feed_token(next(tokens))
    return ip


@pytest.mark.parametrize("k", range(N_TOKENS + 1))
@pytest.mark.parametrize("copy_first", [True, False])
def test_copy_from_any_point_finishes_on_its_own(parser, k, copy_first):
    want = parser.parse(TEXT)
    ip = advanced(parser, k)
    twin = ip.copy()
    first, second = (twin, ip) if copy_first else (ip, twin)
    assert first.resume_parse() == want
    assert second.resume_parse() == want


@pytest.mark.parametrize("k", [0, 5, N_TOKENS])
def test_copy_of_a_copy(parser, k):
    want = parser.parse(TEXT)
    ip = advanced(parser, k)
    a = ip.copy()
    b = a.copy()
    assert b.resume_parse() == want
    assert a.resume_parse() == want
    assert ip.resume_parse() == want


@pytest.mark.parametrize("k", [0, 4, N_TOKENS])
def test_copied_parser_state_is_independent(parser, k):
    want = parser.parse(TEXT)
    ip = advanced(parser, k)
    state = copy(ip.parser_state)
    assert ip.parser.parse_from_state(state) == want
    assert ip.resume_parse() == want


def test_immutable_parser_can_be_finished_twice(parser):
    want = parser.parse(TEXT)
    frozen = parser.parse_interactive(TEXT).as_immutable()
    assert frozen.exhaust_lexer().resume_parse() == want
    assert frozen.exhaust_lexer().resume_parse() == want
