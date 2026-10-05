"""Ours (a hidden test for the code fixer's eval, not upstream's): IndexedSet indexing must agree
with a plain list of the same items, after any mix of removals, for every index in range and out
of it. Same code as the bug report's test, reached with other sizes, removal patterns and
indexes."""
import random

import pytest

from boltons.setutils import IndexedSet


def outcome(fn):
    try:
        return ("ok", fn())
    except IndexError:
        return ("IndexError", None)


def check_same(iset, ref):
    assert list(iset) == ref
    assert len(iset) == len(ref)
    n = len(ref)
    for i in range(-n - 3, n + 3):
        assert outcome(lambda: iset[i]) == outcome(lambda: ref[i]), i
    for v in ref:
        assert iset.index(v) == ref.index(v)


@pytest.mark.parametrize("seed", range(40))
def test_matches_a_list_after_random_removals(seed):
    rng = random.Random(seed)
    size = rng.randint(0, 60)
    iset, ref = IndexedSet(range(size)), list(range(size))
    for _ in range(rng.randint(1, 25)):
        op = rng.random()
        if op < 0.4 and ref:
            v = rng.choice(ref)
            iset.discard(v)
            ref.remove(v)
        elif op < 0.8:
            n = len(ref)
            i = rng.randint(-n - 4, n + 3)
            got, want = outcome(lambda: iset.pop(i)), outcome(lambda: ref.pop(i))
            assert got == want, (i, n)
        else:
            v = rng.randint(0, 200)
            iset.add(v)
            if v not in ref:
                ref.append(v)
        check_same(iset, ref)


def test_out_of_range_never_removes_an_item():
    iset = IndexedSet("abcdefgh")
    iset.discard("c")
    iset.discard("f")
    before = list(iset)
    for i in (6, 7, 50, -7, -8, -50):
        with pytest.raises(IndexError):
            iset.pop(i)
        with pytest.raises(IndexError):
            iset[i]
    assert list(iset) == before


def test_without_removals():
    iset = IndexedSet([10, 20, 30])
    assert iset[-3] == 10
    with pytest.raises(IndexError):
        iset[-4]
    with pytest.raises(IndexError):
        iset.pop(-4)
    assert list(iset) == [10, 20, 30]
