"""Ours (a hidden test for the code fixer's eval, not upstream's): a batch that mixes documents
carrying a free doc_id with plain dicts must store every document under the ID it returns,
overwrite nothing, and leave tinydb able to number every later plain insert(). Random sequences
of batches, plain inserts and removals, in memory and in JSON storage reloaded between steps.

Which IDs tinydb picks is left open: any numbering that reuses no ID in use is valid. Explicit
IDs come first in each batch, so no valid numbering can make a batch collide with itself."""
import random

import pytest

from tinydb import TinyDB
from tinydb.storages import MemoryStorage
from tinydb.table import Document


def open_db(kind, path):
    return TinyDB(path) if kind == "json" else TinyDB(storage=MemoryStorage)


def stored(table):
    return {d.doc_id: dict(d) for d in table.all()}


def insert_plain(table, doc):
    before = stored(table)
    doc_id = table.insert(doc)   # must never fail: the ID is tinydb's to choose
    assert doc_id not in before
    assert stored(table) == {**before, doc_id: doc}


def insert_batch(rng, table, step):
    before = stored(table)
    free = [i for i in range(1, 13) if i not in before]
    explicit = rng.sample(free, k=min(len(free), rng.randint(0, 2)))
    batch = [Document({"step": step, "id": i}, doc_id=i) for i in explicit]
    batch += [{"step": step, "j": j} for j in range(rng.randint(1, 3))]
    ids = table.insert_multiple(batch)
    assert len(ids) == len(batch) == len(set(ids)), ids
    assert ids[:len(explicit)] == explicit
    assert not set(ids) & set(before), (ids, sorted(before))
    assert stored(table) == {**before, **{i: dict(doc) for i, doc in zip(ids, batch)}}


@pytest.mark.parametrize("kind", ["memory", "json"])
@pytest.mark.parametrize("seed", range(60))
def test_mixed_batches_overwrite_nothing(tmp_path, kind, seed):
    rng = random.Random(seed)
    path = str(tmp_path / "db.json")
    db = open_db(kind, path)
    try:
        for step in range(rng.randint(1, 8)):
            if kind == "json" and rng.random() < 0.4:
                db.close()
                db = open_db(kind, path)
            table = db.table("t")
            op = rng.random()
            if op < 0.5:
                insert_batch(rng, table, step)
            elif op < 0.8:
                insert_plain(table, {"plain": step})
            elif len(table):
                table.remove(doc_ids=[rng.choice(sorted(stored(table)))])
        for k in range(6):
            insert_plain(db.table("t"), {"last": k})
    finally:
        db.close()


def test_explicit_id_first_then_plain_inserts():
    db = TinyDB(storage=MemoryStorage)
    t = db.table("t")
    ids = t.insert_multiple([Document({"x": 1}, doc_id=5), {"y": 1}, {"y": 2}])
    assert ids[0] == 5 and len(set(ids)) == 3
    for k in range(8):
        insert_plain(t, {"z": k})
    assert len(t) == 11
