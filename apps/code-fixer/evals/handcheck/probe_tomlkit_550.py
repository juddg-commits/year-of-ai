"""tomlkit-550: dotted-key tables grow [table] or [[array]] children, then new top-level keys are
added. Records the text tomlkit writes and whether it reads back as the same data."""
import json
import random

import tomlkit
from tomlkit import parse

SOURCES = ["a.b = 1\n", "a.b.c = 1\n", "x = 0\na.b = 1\n", "a.b = 1\nq.r = 2\n",
           "a.b.c = 1\na.b.d = 2\n", "p = 1\nq.r.s = 2\n"]


def child(rng):
    r = rng.random()
    if r < 0.4:
        return {"k": rng.randint(0, 9)} if rng.random() < 0.5 else {}
    if r < 0.7:
        aot = tomlkit.aot()
        for _ in range(rng.randint(1, 2)):
            aot.append({"n": rng.randint(0, 9)})
        return aot
    return rng.randint(0, 9)


def tables(container, prefix=()):
    for key, value in container.items():
        if isinstance(value, tomlkit.items.Table):
            yield prefix + (key,), value
            yield from tables(value, prefix + (key,))


results = []
for seed in range(500):
    rng = random.Random(seed)
    src = rng.choice(SOURCES)
    doc = parse(src)
    last = list(doc)[-1]
    found = [t for p, t in tables(doc) if p[0] == last]
    log = [src]
    try:
        for step in range(rng.randint(0, 3)):
            if found:
                rng.choice(found)[f"h{step}"] = child(rng)
        for step in range(rng.randint(1, 3)):
            doc[f"z{step}"] = step
        text = doc.as_string()
        try:
            same = parse(text).unwrap() == doc.unwrap()
        except Exception as e:
            same = type(e).__name__
        log += [text, same]
    except Exception as e:
        log += [type(e).__name__]
    results.append(log)
print(json.dumps(results, default=str))
