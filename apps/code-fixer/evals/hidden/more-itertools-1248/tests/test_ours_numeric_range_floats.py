"""Ours (a hidden test for the code fixer's eval, not upstream's): with float steps, numeric_range
must agree with the items it produces. len(), 'in', index(), count() and indexing are
checked against list(r), on random ranges whose stop often lands on an item boundary."""
import random

import pytest

from more_itertools import numeric_range

STEPS = [0.1, 0.2, 0.3, 0.7, 1.1, 0.05, 0.25, 1 / 3, -0.1, -0.3, -0.7, -1.1, -0.05, -0.25]


def random_ranges(seed, count=50):
    rng = random.Random(seed)
    for _ in range(count):
        step = rng.choice(STEPS)
        start = round(rng.uniform(-3, 3), rng.choice([0, 1, 2]))
        stop = start + rng.randint(0, 25) * step
        stop = rng.choice([stop, round(stop, 2), round(stop, 1), stop + step / 2])
        yield start, stop, step


def candidates(items, start, stop, step):
    """Values that may or may not be items: the items, their tidy roundings, midpoints, edges."""
    out = [start, stop, start - step, stop - step]
    for i, x in enumerate(items):
        out += [x, round(x, 1), round(x, 2)]
        if i:
            out.append((items[i - 1] + x) / 2)
    return out


@pytest.mark.parametrize("seed", range(20))
def test_agrees_with_its_own_items(seed):
    for start, stop, step in random_ranges(seed):
        r = numeric_range(start, stop, step)
        items = list(r)
        where = (start, stop, step)
        assert len(r) == len(items), where
        assert bool(r) == bool(items), where
        for i, x in enumerate(items):
            assert r[i] == x, where
            assert x in r, (where, x)
            assert r.index(x) == i, (where, x)
            assert r.count(x) == 1, (where, x)
        for x in candidates(items, start, stop, step):
            assert (x in r) == (x in items), (where, x)
            assert r.count(x) == items.count(x), (where, x)
            if x not in items:
                with pytest.raises(ValueError):
                    r.index(x)


def test_tenths():
    r = numeric_range(0.0, 1.0, 0.1)
    items = list(r)
    assert len(r) == len(items) == 10
    assert 0.3 not in r and 0.30000000000000004 in r
    assert r.index(items[3]) == 3
