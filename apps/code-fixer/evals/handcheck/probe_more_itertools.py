"""more-itertools-1285 probe: random data and random bucket operations (membership, key listing,
partial and full reads), with and without a validator. Per trial: every result, in order."""
import json, random
from more_itertools import bucket
rng = random.Random(0)
out = []
for trial in range(400):
    data = [rng.choice("abcde") + str(rng.randint(0, 3)) for _ in range(rng.randint(0, 12))]
    valid = None if trial % 3 else (lambda k: k in "abc")
    b = bucket(iter(data), key=lambda s: s[0], validator=valid)
    log, open_iters = [], {}
    for step in range(rng.randint(1, 8)):
        op, k = rng.random(), rng.choice("abcdefz")
        if op < 0.3: log.append(["in", k, k in b])
        elif op < 0.5: log.append(["keys", sorted(b)])
        elif op < 0.75:
            it = open_iters.setdefault(k, b[k])
            log.append(["next", k, next(it, None)])
        else: log.append(["list", k, list(b[k])])
    log.append(["keys_end", sorted(b)])
    out.append(log)
print(json.dumps(out))
