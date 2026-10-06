"""more-itertools-1248: numeric_range with float steps. len(), 'in', index() and r[i] must agree
with the items the range produces."""
import json
import random

from more_itertools import numeric_range


def outcome(fn):
    try:
        return ["ok", fn()]
    except Exception as e:
        return [type(e).__name__]


results = []
for seed in range(600):
    rng = random.Random(seed)
    start = round(rng.uniform(-5, 5), rng.choice([0, 1, 2, 3]))
    step = round(rng.uniform(0.01, 2), rng.choice([1, 2, 3])) or 0.1
    step *= rng.choice([1, -1])
    count = rng.randint(0, 30)
    stop = start + step * count + rng.choice([0.0, step / 2, -step / 3, 1e-12, -1e-12])
    r = numeric_range(start, stop, step)
    items = list(r)
    probes = items + [x + step / 2 for x in items[:3]] + [start - step, stop, stop + step]
    results.append([start, stop, step, items, outcome(lambda: len(r)),
                    [x in r for x in probes], [outcome(lambda: r.index(x)) for x in probes],
                    [outcome(lambda: r[i]) for i in range(-len(items) - 2, len(items) + 2)]])
print(json.dumps(results))
