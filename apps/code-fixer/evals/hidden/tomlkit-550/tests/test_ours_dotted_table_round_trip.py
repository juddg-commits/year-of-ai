"""Ours (a hidden test for the code fixer's eval, not upstream's): whatever a document holds, a key
added to it must stay where it was added once the document is written out and read back. Dotted
keys at several depths whose tables grow [table] or [[array of tables]] children, then new keys
after them, checked by round trip only (where the new key is written is up to tomlkit). Out of
scope, as separate bugs: a table that grows a header while other keys already follow it, and any
of this inside a [table] (the new sub-table gets the wrong header)."""
import random

import pytest

import tomlkit
from tomlkit import parse


def round_trips(doc):
    text = doc.as_string()
    assert parse(text).unwrap() == doc.unwrap(), text


SOURCES = [
    "a.b = 1\n",
    "a.b.c = 1\n",
    "x = 0\na.b = 1\n",
    "a.b = 1\nq.r = 2\n",
]


def table_header_child(rng):
    if rng.random() < 0.5:
        return {"k": rng.randint(0, 9)} if rng.random() < 0.5 else {}
    aot = tomlkit.aot()
    for _ in range(rng.randint(1, 2)):
        aot.append({"n": rng.randint(0, 9)})
    return aot


def dotted_tables(container, prefix=()):
    """Every table under the container, as (path, table)."""
    for key, value in container.items():
        if isinstance(value, tomlkit.items.Table):
            yield prefix + (key,), value
            yield from dotted_tables(value, prefix + (key,))


@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("seed", range(12))
def test_new_keys_stay_where_they_were_added(source, seed):
    rng = random.Random(seed)
    doc = parse(source)
    last = list(doc)[-1]
    tables = [table for path, table in dotted_tables(doc) if path[0] == last]
    assert tables, source
    for step in range(rng.randint(0, 3)):
        rng.choice(tables)[f"h{step}"] = table_header_child(rng)
    for step in range(rng.randint(1, 3)):
        doc[f"z{step}"] = step
        round_trips(doc)


def test_deep_dotted_key_grows_a_table():
    doc = parse("a.b.c = 1\n")
    doc["a"]["b"]["d"] = {"e": 1}
    doc["z"] = 2
    assert parse(doc.as_string()) == {"z": 2, "a": {"b": {"c": 1, "d": {"e": 1}}}}


def test_dotted_key_grows_an_array_of_tables():
    doc = parse("a.b = 1\n")
    aot = tomlkit.aot()
    aot.append({"n": 1})
    doc["a"]["list"] = aot
    doc["z"] = 2
    assert parse(doc.as_string()) == {"z": 2, "a": {"b": 1, "list": [{"n": 1}]}}

