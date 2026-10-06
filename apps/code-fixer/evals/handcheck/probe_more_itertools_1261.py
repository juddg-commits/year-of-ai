"""more-itertools-1261: combination_with_replacement_index with None and duplicates in the pool,
for valid combinations and invalid ones."""
import json
import random

from more_itertools import combination_with_replacement_index

VALUES = [None, 0, 1, 2, "a", "b", 3.5, (1, 2)]


def outcome(fn):
    try:
        return ["ok", fn()]
    except Exception as e:
        return [type(e).__name__]


results = []
for seed in range(800):
    rng = random.Random(seed)
    pool = [rng.choice(VALUES) for _ in range(rng.randint(0, 6))]
    r = rng.randint(0, 4)
    if pool and rng.random() < 0.7:
        element = [pool[i] for i in sorted(rng.choices(range(len(pool)), k=r))]
    else:
        element = [rng.choice(VALUES + ["z"]) for _ in range(r)]
    results.append([repr(pool), repr(element), outcome(lambda: combination_with_replacement_index(element, pool))])
print(json.dumps(results))
