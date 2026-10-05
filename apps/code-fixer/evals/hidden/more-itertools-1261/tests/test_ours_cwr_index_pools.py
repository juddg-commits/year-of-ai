"""Ours (a hidden test for the code fixer's eval, not upstream's): combination_with_replacement_index
must give each combination's position in itertools.combinations_with_replacement, for pools that
hold None, other falsy values, or neither, and for inputs that are one-shot iterators. Anything
that isn't such a combination raises ValueError."""
from itertools import combinations_with_replacement

import pytest

from more_itertools import combination_with_replacement_index

POOLS = [
    (None,),
    (None, "a"),
    ("a", None),
    (0, None, "b"),
    ("", (), None, "x"),
    (1, 2, 3, None),
    (None, 1, 2, 3),
    ("a", "b", "c"),
]


@pytest.mark.parametrize("pool", POOLS)
@pytest.mark.parametrize("r", range(4))
def test_every_combination_has_its_position(pool, r):
    for i, combo in enumerate(combinations_with_replacement(pool, r)):
        assert combination_with_replacement_index(combo, pool) == i, (combo, pool)
        assert combination_with_replacement_index(iter(combo), iter(pool)) == i, (combo, pool)


@pytest.mark.parametrize("pool", POOLS)
def test_not_a_combination(pool):
    with pytest.raises(ValueError):
        combination_with_replacement_index(("not in the pool",), pool)
    with pytest.raises(ValueError):
        combination_with_replacement_index((pool[0], "not in the pool"), pool)
    if len(pool) > 1:
        with pytest.raises(ValueError):   # out of the pool's order
            combination_with_replacement_index((pool[-1], pool[0]), pool)
