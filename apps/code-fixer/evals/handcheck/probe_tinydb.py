"""tinydb-633 probe: random insert / insert_multiple / remove sequences, mixing documents with an
explicit doc_id and plain dicts, in memory and in JSON storage (reloaded between batches). Per
trial: every result in order, the final table, and the overwrite count: a batch that returns one
ID twice (the later document overwrote the earlier one) or a stored table that differs from what
was inserted."""
import json, random, tempfile, os
from tinydb import TinyDB
from tinydb.storages import MemoryStorage
from tinydb.table import Document

rng = random.Random(0)
out = []
for trial in range(300):
    path = None
    if trial % 2:
        fd, path = tempfile.mkstemp(suffix=".json"); os.close(fd); os.unlink(path)
        db = TinyDB(path)                  # JSON storage: keys come back as strings
    else:
        db = TinyDB(storage=MemoryStorage)
    t = db.table("t")
    log, shadow, overwrites = [], {}, 0
    for step in range(rng.randint(1, 6)):
        if trial % 2 and rng.random() < 0.3:
            db.close(); db = TinyDB(path); t = db.table("t")   # reload from disk between batches
        op = rng.random()
        if op < 0.3:
            try:
                i = t.insert({"v": step}); shadow[i] = {"v": step}; log.append(["insert", i])
            except ValueError as e: log.append(["insert_err", str(e)])
        elif op < 0.45:
            did = rng.randint(1, 12)
            try:
                i = t.insert(Document({"v": step}, doc_id=did)); shadow[i] = {"v": step}; log.append(["insert_doc", i])
            except ValueError as e: log.append(["insert_doc_err", str(e)])
        else:
            batch = []
            for j in range(rng.randint(1, 4)):
                if rng.random() < 0.4: batch.append(Document({"v": step, "j": j}, doc_id=rng.randint(1, 12)))
                else: batch.append({"v": step, "j": j})
            try:
                ids = t.insert_multiple(batch)
                overwrites += len(ids) - len(set(ids))
                for i, d in zip(ids, batch): shadow[i] = dict(d)
                log.append(["insert_multiple", ids])
            except ValueError as e: log.append(["insert_multiple_err", str(e)])
        if rng.random() < 0.2 and len(t):
            gone = rng.choice([d.doc_id for d in t.all()]); t.remove(doc_ids=[gone]); shadow.pop(gone, None)
            log.append(["removed"])
        stored = {d.doc_id: dict(d) for d in t.all()}
        if stored != shadow:
            overwrites += 1; shadow = stored      # resync, so one bug counts once
    log.append(["final", sorted([d.doc_id, dict(d)] for d in t.all())])
    log.append(["overwrites", overwrites])
    out.append(log)
    db.close()
    if path: os.unlink(path)
print(json.dumps(out))
