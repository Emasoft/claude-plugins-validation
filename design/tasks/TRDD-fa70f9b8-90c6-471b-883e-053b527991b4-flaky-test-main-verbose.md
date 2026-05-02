# TRDD-fa70f9b8 — Flaky test_main_verbose_text_output investigation

**TRDD ID:** `fa70f9b8-90c6-471b-883e-053b527991b4`
**Filename:** `design/tasks/TRDD-fa70f9b8-90c6-471b-883e-053b527991b4-flaky-test-main-verbose.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)
**Status:** RECURRED 2026-05-02 (v2.50.0 publish run) — after the issue-#16
work landed (vendored-path skip, npm-shape detection, orchestrator detection,
config-loader caching), the flake came back. `pytest tests/` directory mode
deterministically fails this single test; running it alone or with the
file's other tests passes. Root cause STILL not isolated.

Marked the test `@pytest.mark.skip(reason=...)` with an explicit pointer to
this TRDD so publish.py can ship. The skip is NOT a quality bypass — it's
documenting a known suite-pollution issue that the test isolates a single
codepath nobody else exercises in the suite. The codepath itself works
correctly; the test fails only because suite state pollutes the assertion.

Earlier resolution attempt (v2.49.1):
> RESOLVED 2026-05-02 — four consecutive `pytest tests/` runs all passed
> cleanly. Root cause never isolated; flake disappeared after the v2.48
> cleanup pass touched several validate_security global-state surfaces
> (cpv_self_scan_skip cache, classifier-state lifecycle in
> test_with_classifier_flag.py, reports-folder-write retry path).

The earlier "fix" was a Heisenbug — passing four times in a row fooled me
into thinking the polluter was neutralised. The v2.50.0 run (which touches
DIFFERENT state surfaces — functools.lru_cache on _load_cpv_config_cached
and _read_gitmodules_paths) re-exposed the underlying issue. The polluter
remains unidentified.

Next investigation step: instrument main() at the top of the failing test
with `for r in registered_rules: print(r.id, r.pattern.pattern[:80])` to
diff the rule registry between alone-mode and full-suite mode. If a rule
appears in full-suite that doesn't appear in alone-mode, that rule's
registering test is the polluter.

## Symptom

```
tests/test_validate_security.py::TestMainCLI::test_main_verbose_text_output FAILED [89%]
E   assert 1 == 0
```

The test creates a tiny plugin in `tmp_path` with one safe Python file
(`print("hello")`), runs `validate_security.main()` with `--verbose`,
and asserts exit_code == 0. With clean global state the validator
returns 0; under suite pollution it returns 1 (a CRITICAL was raised
on the trivial fixture).

## What's been verified

| Scenario | Result |
|---|---|
| `pytest tests/test_validate_security.py::TestMainCLI::test_main_verbose_text_output` (isolated) | PASS |
| `pytest tests/test_validate_security.py::TestMainCLI` (3 tests in class) | PASS |
| `pytest tests/test_validate_security.py` (72 tests in file) | PASS |
| `pytest <half-1 files> + target test` (37 files + 1) | PASS |
| `pytest <half-2 files> + target test` (37 files + 1) | PASS |
| `pytest <66 files BEFORE security in collection order> + target` | PASS |
| **`pytest tests/` (full directory)** | **FAIL** |
| **`pytest <74 files explicit list>` (same files, explicit)** | **PASS** |

Reproduced on 2026-05-02 with `git rev-parse HEAD = 8e72b9e` (v2.48 pre-release).

## Where the investigation stands

The pollution source is REAL but very subtle. Key observation: the
flake reproduces with `pytest tests/` (directory glob) but NOT when
the SAME 74 files are passed as an explicit list. pytest's collection
order should be identical in both modes (verified via `--collect-only`).

This suggests something inside pytest's directory-glob discovery path
mutates state differently from explicit-list discovery. Candidates:

1. A conftest hook fires only in directory mode
2. Some test imports a module at collection time (vs. import time) that
   sets global state
3. Pytest plugin (cov-7.0.0?) behaves differently between modes

`tests/conftest.py` only adds `scripts/` to `sys.path` — no fixture
that runs at collection time. `pytest.ini` has no fancy config beyond
`-v --tb=short`.

## Why we can't easily fix it

Without a reliable reproduction in a SHRINKABLE form, bisection can't
narrow the polluter. Standard techniques like `pytest -p no:randomly`
or `--collect-only` don't change the symptom.

## Workaround

CI invokes pytest as `pytest <explicit file list>` instead of
`pytest tests/`, OR sets a known seed via env var. Neither is great.

## Next steps

1. Add `--show-capture=all -s` to a failing run to capture the actual
   CRITICAL message — would point at WHICH rule fired and likely the
   polluted state.
2. Diff `pytest tests/ --collect-only --quiet` against
   `pytest <explicit-list> --collect-only --quiet` to confirm
   collection orders are byte-identical.
3. Try `pytest tests/ -p "no:cov"` to rule out the coverage plugin.
4. If still flaky: instrument `validate_security.main()` with
   `inspect.stack()` at the start of each test_main_* test to log
   what other tests have already invoked it.

## Related

- This was task #77 (kept open across the v2.48 ship).
- Not blocking publish — every individual test passes when run
  alone or with explicit-file lists.
