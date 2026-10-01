"""The mutant generator (offline): each kind of slip, edits that apply exactly once, and every
mutant of the real sources in evals/."""

import ast
import unittest

from code_fixer import cases, mutate

SAMPLE = '''\
def window(items, size):
    """Items before `size`, plus one."""
    out = []
    for i in range(len(items)):
        if i < size and not items[i] is None:
            out.append(items[i] + 1)
    total = 0
    total += len(out)
    return out


LIMIT = 3
'''

REPEATS = '''\
def f(a):
    a.x = 1
    a.y = 2
    a.x = 1
    return a
'''


def applied(text: str, m: mutate.Mutant) -> str:
    """The way cases.materialize plants a bug."""
    assert text.count(m.find) == 1
    return text.replace(m.find, m.replace)


class Mutants(unittest.TestCase):
    def test_each_kind_of_slip(self):
        found = {(m.kind, m.after) for m in mutate.mutants(SAMPLE, "w.py")}
        for expected in [("boundary", "if i <= size and not items[i] is None:"),
                         ("flip", "if i > size and not items[i] is None:"),
                         ("bool", "if i < size or not items[i] is None:"),
                         ("not", "if i < size and items[i] is None:"),
                         ("negate", "if i < size and not items[i] is not None:"),
                         ("arith", "out.append(items[i] - 1)"),
                         ("arith", "total -= len(out)"),
                         ("const", "out.append(items[i] + 2)"),
                         ("const", "out.append(items[i] + 0)"),
                         ("const", "LIMIT = 4")]:
            self.assertIn(expected, found)

    def test_only_state_updates_and_calls_inside_functions_are_deleted(self):
        deleted = sorted(m.before for m in mutate.mutants(SAMPLE, "w.py") if m.kind == "delete")
        self.assertEqual(deleted, ["out = []", "total += len(out)", "total = 0"])   # no docstring, no LIMIT, no lone if-body

    def test_every_edit_changes_only_its_own_line(self):
        for m in mutate.mutants(SAMPLE, "w.py"):
            out = applied(SAMPLE, m)
            self.assertNotEqual(ast.dump(ast.parse(out)), ast.dump(ast.parse(SAMPLE)))
            if m.kind == "delete":
                self.assertEqual(out.count("\n"), SAMPLE.count("\n") - 1)
            else:
                self.assertEqual(out.splitlines()[m.line - 1].strip(), m.after)

    def test_a_repeated_line_gets_enough_context_to_be_unique(self):
        firsts = [m for m in mutate.mutants(REPEATS, "r.py") if m.line == 2 and m.after == "a.x = 2"]
        self.assertEqual(len(firsts), 1)
        self.assertEqual(applied(REPEATS, firsts[0]).splitlines()[1:4], ["    a.x = 2", "    a.y = 2", "    a.x = 1"])

    def test_overlapping_copies_dont_count_as_unique(self):
        self.assertFalse(mutate._only_at("a\na\na\n", 0, 4))   # "a\na\n" also starts at 2; str.count says 1
        self.assertTrue(mutate._only_at("a\nb\na\n", 0, 4))

    def test_every_mutant_of_the_real_sources_applies_once(self):
        for source, rel in [("cachetools", "src/cachetools/__init__.py"), ("parse", "parse/__init__.py")]:
            text = (cases.SOURCES_DIR / source / rel).read_text()
            found = mutate.mutants(text, rel)
            self.assertGreater(len(found), 100)
            for m in found:
                ast.parse(applied(text, m))


def row(mid, kind, function, visible=1, hidden=2, errors=()):
    return {"id": mid, "status": "caught", "kind": kind, "file": "m.py", "function": function,
            "visible_failing": visible, "hidden_failing": hidden, "errors": list(errors), "shows_line": False,
            "before": "a < b", "after": "a <= b"}


class Picking(unittest.TestCase):
    def test_one_per_function_varied_by_kind(self):
        rows = [row("f-delete-loud", "delete", "f", visible=3),
                row("f-delete", "delete", "f"),                       # f's weakest deletion
                row("g-delete", "delete", "g", errors=["NameError"]),  # the error names the cause
                row("f-negate", "negate", "f"),                       # f is taken by then
                row("h-negate", "negate", "h"),
                row("k-const", "const", "k", hidden=0)]              # no hidden test catches it
        for seed in range(5):
            self.assertEqual(sorted(r["id"] for r in mutate.pick(rows, 10, seed)), ["f-delete", "h-negate"])

    def test_a_bug_that_names_its_cause_or_shows_its_line_is_left_out(self):
        rows = [row("a", "delete", "f", errors=["AttributeError"]),   # a deleted self.x = …
                row("b", "negate", "g", errors=["AttributeError"]),   # a symptom, not the cause
                {**row("c", "const", "h"), "shows_line": True},
                {**row("d", "const", "k"), "before": "misses += 1", "after": "misses += 0"},
                {**row("e", "const", "m"), "before": "x[1:]", "after": "x[0:]"}]
        self.assertEqual(sorted(r["id"] for r in mutate.pick(rows, 10, 0)), ["b", "e"])

    def test_errors_come_from_tracebacks_and_uncut_summary_lines(self):
        output = ("E   NameError: name 'k' is not defined\n"
                  "FAILED tests/test_a.py::test_x - UnboundLocalError: cannot access…\n"
                  "FAILED tests/test_a.py::TestLongName::test_with_a_long_name - Ass...\n"
                  "PASSED tests/test_a.py::test_y\n")
        self.assertEqual(mutate.errors_in(output), ["NameError", "UnboundLocalError"])


if __name__ == "__main__":
    unittest.main()
