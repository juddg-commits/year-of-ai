"""Ours (a hidden test for the code fixer's eval, not upstream's): looking things up in a bucket
must never invent a key. Random interleavings of 'in', partial reads of b[key] and full listing,
with and without a validator, checked against a plain model of the items not yet taken."""
import random
from collections import defaultdict

import pytest

from more_itertools import bucket

LETTERS = "abcdx"   # "x" never occurs


def random_items(rng):
    return [f"{rng.choice('abcd')}{i}" for i in range(rng.randint(0, 12))]


@pytest.mark.parametrize("seed", range(60))
@pytest.mark.parametrize("with_validator", [False, True])
def test_lookups_never_invent_keys(seed, with_validator):
    rng = random.Random(seed)
    items = random_items(rng)
    valid = (lambda k: k != "c") if with_validator else (lambda k: True)
    b = bucket(items, key=lambda s: s[0], validator=valid if with_validator else None)
    left = defaultdict(list)
    for item in items:
        left[item[0]].append(item)
    present = {item[0] for item in items if valid(item[0])}
    for _ in range(rng.randint(0, 10)):
        k = rng.choice(LETTERS)
        if rng.random() < 0.5:
            assert (k in b) == (valid(k) and bool(left[k])), (items, k)
        else:
            for _ in range(rng.randint(1, 3)):
                got = next(b[k], None)
                want = left[k].pop(0) if valid(k) and left[k] else None
                assert got == want, (items, k)
    assert set(b) == present, items


def test_only_misses():
    b = bucket(["a1", "b1"], key=lambda s: s[0])
    assert "x" not in b
    assert list(b["y"]) == []
    assert next(b["z"], None) is None
    assert sorted(b) == ["a", "b"]


def test_a_key_read_to_the_end_is_still_a_key():
    b = bucket(["a1", "b1", "a2"], key=lambda s: s[0])
    assert list(b["a"]) == ["a1", "a2"]
    assert "a" not in b          # nothing left under it
    assert sorted(b) == ["a", "b"]
