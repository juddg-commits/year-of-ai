"""Ours (a hidden test for the code fixer's eval, not upstream's): wraps(donor)(wrapper) must reach
a wrapper whose arguments are keyword-only, whatever the donor's signature looks like. Every small
shape: one to three arguments, any number of donor defaults, the keyword-only split anywhere,
with and without *varargs, and an async wrapper too."""
import asyncio
import inspect
import itertools

import pytest

from boltons.funcutils import wraps


def make(src, name):
    ns = {}
    exec(src, ns)
    return ns[name]


def shapes():
    for n in (1, 2, 3):
        names = [f"p{i}" for i in range(n)]
        for n_defaults, split, varargs in itertools.product(range(n + 1), range(n + 1), (False, True)):
            yield names, n_defaults, split, varargs


def sources(names, n_defaults, split, varargs, is_async=False):
    params = [f"{p}={100 + i}" if i >= len(names) - n_defaults else p for i, p in enumerate(names)]
    donor = ", ".join(params + (["*va"] if varargs else []))
    head = names[:split]
    tail = names[split:]
    star = ["*va"] if varargs else (["*"] if tail else [])
    wrapper = ", ".join(head + star + tail)
    returned = "(" + "".join(f"{p}, " for p in names) + ("va" if varargs else "()") + ")"
    prefix = "async " if is_async else ""
    return (f"{prefix}def donor({donor}):\n    raise AssertionError('never called')\n",
            f"{prefix}def wrapper({wrapper}):\n    return {returned}\n")


def calls(names, n_defaults, varargs):
    required = len(names) - n_defaults
    yield tuple(range(1, required + 1)), {}
    yield tuple(range(1, len(names) + 1)), {}
    if varargs:
        yield tuple(range(1, len(names) + 3)), {}
    else:
        yield (), {p: i for i, p in enumerate(names, 1)}


def expected(donor, args, kwargs, names, varargs):
    bound = inspect.signature(donor).bind(*args, **kwargs)
    bound.apply_defaults()
    return tuple(bound.arguments[p] for p in names) + (tuple(bound.arguments.get("va", ())),)


@pytest.mark.parametrize("names,n_defaults,split,varargs", list(shapes()))
def test_every_small_shape(names, n_defaults, split, varargs):
    donor_src, wrapper_src = sources(names, n_defaults, split, varargs)
    donor, wrapper = make(donor_src, "donor"), make(wrapper_src, "wrapper")
    wrapped = wraps(donor)(wrapper)
    assert inspect.signature(wrapped) == inspect.signature(donor)
    for args, kwargs in calls(names, n_defaults, varargs):
        got = wrapped(*args, **kwargs)
        want = expected(donor, args, kwargs, names, varargs)
        if not varargs:
            got = got[:-1] + ((),)
        assert got == want, (donor_src, wrapper_src, args, kwargs)


@pytest.mark.parametrize("varargs", (False, True))
def test_async_wrapper(varargs):
    names = ["p0", "p1", "p2"]
    donor_src, wrapper_src = sources(names, 1, 1, varargs, is_async=True)
    donor, wrapper = make(donor_src, "donor"), make(wrapper_src, "wrapper")
    wrapped = wraps(donor)(wrapper)
    assert inspect.iscoroutinefunction(wrapped)
    for args, kwargs in calls(names, 1, varargs):
        got = asyncio.run(wrapped(*args, **kwargs))
        want = expected(donor, args, kwargs, names, varargs)
        if not varargs:
            got = got[:-1] + ((),)
        assert got == want, (args, kwargs)


def test_keyword_only_with_its_own_default_is_still_overridden():
    def donor(a, b=1):
        raise AssertionError('never called')

    def wrapper(a, *, b=99):
        return a, b

    assert wraps(donor)(wrapper)(5) == (5, 1)
    assert wraps(donor)(wrapper)(5, 7) == (5, 7)
    assert wraps(donor)(wrapper)(a=5, b=7) == (5, 7)
