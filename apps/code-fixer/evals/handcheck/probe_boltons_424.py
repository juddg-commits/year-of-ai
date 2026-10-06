"""boltons-424: IndexedSet indexing, pop and slicing after random removals and additions."""
import json
import random

from boltons.setutils import IndexedSet


def clean(v):
    """Items are ints; anything else (the bug can hand back its dead-slot marker) is kept as its repr."""
    if isinstance(v, list):
        return [clean(x) for x in v]
    return v if isinstance(v, int) else repr(v)


def outcome(fn):
    try:
        return ["ok", clean(fn())]
    except Exception as e:
        return [type(e).__name__]


results = []
for seed in range(400):
    rng = random.Random(seed)
    s = IndexedSet(range(rng.randint(0, 40)))
    log = []
    for _ in range(rng.randint(1, 30)):
        op, n = rng.random(), len(s)
        if op < 0.25 and n:
            v = rng.choice(list(s))
            s.discard(v)
            log.append(["discard", v])
        elif op < 0.45:
            i = rng.randint(-n - 4, n + 3)
            log.append(["pop", i, outcome(lambda: s.pop(i))])
        elif op < 0.6:
            v = rng.randint(0, 80)
            s.add(v)
            log.append(["add", v])
        elif op < 0.8:
            i = rng.randint(-n - 4, n + 3)
            log.append(["get", i, outcome(lambda: s[i])])
        else:
            a, b, st = rng.randint(-n - 3, n + 3), rng.randint(-n - 3, n + 3), rng.choice([None, 1, 2, 3])
            log.append(["slice", a, b, st, outcome(lambda: list(s[a:b:st]))])
        log.append(["state", list(s), len(s)])
    results.append(log)
print(json.dumps(results))
