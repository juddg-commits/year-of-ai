"""Ours (a hidden test for the code fixer's eval, not upstream's): a streamed item must equal the
same element parsed without streaming, at item depths 1, 2 and 3, with each option that changes
how text is kept: whitespace stripping, force_cdata, the text key, the attribute prefix, the
separator for split text and dropping attributes."""
import itertools
import random

import pytest

from xmltodict import parse

OPTIONS = [
    dict(strip_whitespace=s, force_cdata=f, cdata_key=k, attr_prefix=p, cdata_separator=sep, xml_attribs=a)
    for s, f, k, p, sep, a in itertools.product(
        (True, False), (False, True), ("#text", "_t"), ("@", ""), ("", "|"), (True, False))
]


def streamed(xml, depth, **options):
    items = []

    def keep(path, item):
        items.append(item)
        return True

    parse(xml, item_depth=depth, item_callback=keep, **options)
    return items


def as_list(value):
    return value if isinstance(value, list) else [value]


def random_item(rng, i):
    attrs = f' id="{i}"' if rng.random() < 0.5 else ""
    parts = [f"<c{j}>v{j}</c{j}>" for j in range(rng.randint(0, 2))]
    for _ in range(rng.randint(0, 2)):
        parts.insert(rng.randint(0, len(parts)), rng.choice(["", " ", "txt", "a b", "\n x \n"]))
    return f"<item{attrs}>{''.join(parts)}</item>"


def random_doc(rng):
    groups = []
    for _ in range(rng.randint(1, 3)):
        groups.append("<g>" + "".join(random_item(rng, i) for i in range(rng.randint(1, 3))) + "</g>")
    return "<r>" + "".join(groups) + "</r>"


@pytest.mark.parametrize("options", OPTIONS, ids=lambda o: "-".join(str(v) for v in o.values()))
def test_streamed_items_match_the_whole_parse(options):
    rng = random.Random(str(sorted(options.items())))
    for _ in range(25):
        xml = random_doc(rng)
        whole = parse(xml, **options)
        assert streamed(xml, 1, **options) == [whole["r"]], xml
        assert streamed(xml, 2, **options) == as_list(whole["r"]["g"]), xml
        items = [item for g in as_list(whole["r"]["g"]) for item in as_list(g["item"])]
        assert streamed(xml, 3, **options) == items, xml


def test_text_after_a_child_at_depth_three():
    xml = '<r><g><item k="v"><c>x</c>tail</item></g></r>'
    assert streamed(xml, 3) == [{"@k": "v", "c": "x", "#text": "tail"}]
