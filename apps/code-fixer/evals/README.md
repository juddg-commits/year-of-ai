# Eval cases and sources

Each file in `cases/` is one bug: which source project it lives in, the edits that plant it, the test files the agent never sees (`hidden`), and a note on what's wrong. The note is for grading and write-ups; the agent never sees it, or anything else in this folder.

There are three kinds:
- **real**: a fix merged on GitHub after the model's training data (July to September 2026), undone. The fix's own tests are the bug report. Made with `python real_bugs.py add`.
- **planted**: a one-token slip in well-tested MIT code, generated and screened with `python mutants.py` (see below), or written by hand.
- **handwritten**: bugs in a small project written for this eval (`sources/timesheet/`).

Cases are split into `dev` (tune on it) and `test` (held out: run once, at the end, and report that number). The split was drawn with a fixed seed before any case was run.

`python eval.py verify` checks every case in the sandbox before it's used: the source without the bug passes all of its tests, visible and hidden, and the bug fails at least one visible test.

## Sources

| Folder | Upstream | License | Changes |
|---|---|---|---|
| `sources/cachetools/` | [cachetools](https://github.com/tkem/cachetools) 7.2.0, from its PyPI source release | MIT (`LICENSE` kept) | Only `src/`, `tests/`, `README.rst` and `LICENSE` kept; `pyproject.toml` replaced with a pytest config (`pythonpath = src`). Added `tests/test_ttl_boundary.py` (ours, a hidden test). |
| `sources/parse/` | [parse](https://github.com/r1chardj0n3s/parse) 1.22.2, from its PyPI source release | MIT (`LICENSE` kept) | Only `parse/`, `tests/`, `README.rst` and `LICENSE` kept; `pyproject.toml` replaced with an empty pytest config (upstream's asks for pytest-cov). Added `tests/test_noon_midnight.py` (ours, a hidden test). |
| `sources/timesheet/` | Hand-written for this eval | Same as this app | |

## Adding a case

1. Pick a spot where a real bug could happen, and write the smallest edit that plants it (`find` must occur exactly once in the file).
2. Choose hidden tests that exercise the same code another way, so a fix that only satisfies the visible tests fails. If the source has none, write one and mark it as ours.
3. Run `python eval.py verify --cases <id>` and read what fails.

## Real bugs

Their sources aren't in this repo. `sources.json` pins each one to the fix's merge commit, and `python eval.py fetch` downloads it into `sources/<project>@<commit>/` (git-ignored). Only the package, its tests and its LICENSE are kept, never a changelog that would describe the fix. Each project's own pytest config is replaced with a plain one, so no plugins are needed.

| Case | Fix | License | Split |
|---|---|---|---|
| `tinydb-633` | [msiemens/tinydb#633](https://github.com/msiemens/tinydb/pull/633) | MIT | dev |
| `pyflakes-872` | [PyCQA/pyflakes#872](https://github.com/PyCQA/pyflakes/pull/872) | MIT | dev |
| `networkx-8895` | [networkx/networkx#8895](https://github.com/networkx/networkx/pull/8895) | BSD-3 | dev |
| `more-itertools-1285` | [more-itertools/more-itertools#1285](https://github.com/more-itertools/more-itertools/pull/1285) | MIT | dev |
| `boltons-445` | [mahmoud/boltons#445](https://github.com/mahmoud/boltons/pull/445) | BSD-3 | dev |
| `xmltodict-421` | [martinblech/xmltodict#421](https://github.com/martinblech/xmltodict/pull/421) | MIT | dev |
| `networkx-8734` | [networkx/networkx#8734](https://github.com/networkx/networkx/pull/8734) | BSD-3 | test |
| `more-itertools-1248` | [more-itertools/more-itertools#1248](https://github.com/more-itertools/more-itertools/pull/1248) | MIT | test |
| `more-itertools-1261` | [more-itertools/more-itertools#1261](https://github.com/more-itertools/more-itertools/pull/1261) | MIT | test |
| `boltons-424` | [mahmoud/boltons#424](https://github.com/mahmoud/boltons/pull/424) | BSD-3 | test |
| `tomlkit-550` | [python-poetry/tomlkit#550](https://github.com/python-poetry/tomlkit/pull/550) | MIT | test |
| `lark-1641` | [lark-parser/lark#1641](https://github.com/lark-parser/lark/pull/1641) | MIT | test |

Known limits: most real cases have no hidden tests (the fix's tests sit in one file, which must stay visible), so a fix there is graded on every test in the project rather than on unseen ones. `boltons-445`'s issue has been public since 2020, so the model may have seen the bug, though not the fix.

## Planted bugs, generated

`python mutants.py scan` makes every one-token slip in a source file (a boundary off by one, a flipped or negated comparison, `or` for `and`, a dropped `not`, `-` for `+`, an integer off by one, a missing statement) and runs each against the full test suite in the sandbox. For a mutant the tests catch, the failing test files are hidden except the one with the fewest failures: the agent gets a weak signal, like a real bug report, and the hidden files check that the fix is whole. `python mutants.py pick` then takes one mutant per function, the kinds in turn, and leaves out two easy kinds: a bug whose traceback points at its own line, and one whose error names its cause (a deleted `k = ...` fails with "name 'k' is not defined").

