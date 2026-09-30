# Eval cases and sources

Each file in `cases/` is one bug: which source project it lives in, the edit that plants it, the test files the agent never sees (`hidden`), and a note on what's wrong. The note is for grading and write-ups; the agent never sees it, or anything else in this folder.

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
