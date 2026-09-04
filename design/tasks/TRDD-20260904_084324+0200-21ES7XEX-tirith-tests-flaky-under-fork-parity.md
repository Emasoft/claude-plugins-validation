---
trdd-id: 21ES7XEX
title: Two tirith integration tests fail intermittently under the Gate 3c fork-parity probe and the suite installs a real tirith onto the host
column: todo
created: 2026-09-04T08:43:24+0200
updated: 2026-09-04T10:41:12+0200
current-owner: cpv-main-session
task-type: bugfix
min-approval-requirement: none
relevant-rules: []
---

# Two tirith integration tests are flaky under fork-parity, and the suite mutates the host

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative; supersedes the body) — 2026-09-04

Two independent defects, both in `tests/test_tirith_integration.py` and its
subject `scripts/validate_security.py::check_tirith_scanner`. Neither is caused
by commit `9e7d2c1e`; both predate it.

**NEXT ACTION:** reproduce defect A with instrumentation that records which
branch of `check_tirith_scanner` was taken (the diagnosis pass established
*that* it flakes, never *why*). Fix defect B independently — it needs no repro.

### Defect A — flaky under fork-parity (blocks releases at random)

`scripts/publish.py` Gate 3c (Linux fork-parity probe: the suite re-run under
`-n auto --dist=worksteal` with multiprocessing forced to `fork`) failed a
publish of `9e7d2c1e` with:

- `tests/test_tirith_integration.py::test_check_tirith_top_level_list` (gw8) —
  `assert any("tirith pipe_to_interpreter" in m for m in msgs)` → False
- `tests/test_tirith_integration.py::test_check_tirith_empty_clean_run` (gw10) —
  `assert any("tirith" in m and "no findings" in m for m in passed)` → False

Observed once in three fork-parity probe runs (n=3 — a single failure, not a measured rate):

| # | Run | Result |
|---|---|---|
| 1 | full suite, serial | 13544 passed, 7 skipped, exit 0 |
| 2 | that file alone, serial | 12 passed, exit 0 (571 s) |
| 3 | Gate 3c probe, publish attempt | **2 failed**, 13537 passed, 12 skipped |
| 4 | Gate 3c probe, re-run 1 | 13539 passed, 12 skipped, `PASSED under the Linux fork default` |
| 5 | Gate 3c probe, re-run 2 | 13539 passed, 12 skipped, `PASSED under the Linux fork default` |

Both failing tests drive `check_tirith_scanner` through `_run_with_shim`
(`tests/test_tirith_integration.py:103`), which writes a fake `tirith` shell
shim into `tmp_path/fake-bin` and prepends that dir to `PATH`. The two shapes
that fail are the bare-list JSON payloads (`[{...}]` and `[]`); the object
shapes (`{"findings": …}`, SARIF `{"runs": …}`) passed in the same run. That
correlation is **suspicious but unexplained** — it may be coincidence at n=1.

**Root cause is UNCONFIRMED.** The diagnosis pass produced a plausible story
(resource contention against the 180 s subprocess timeout in
`check_tirith_scanner`, `scripts/validate_security.py:7478`) and explicitly
labelled it unconfirmed. Do not treat it as established. Ruled out first-hand:

- `_resolve_tirith_runner` (`:7381`) has no memoization, so cross-test runner
  poisoning is not the mechanism.
- Commit `9e7d2c1e` does not touch this path — its only
  `scripts/validate_security.py` hunk is at ~9450 (`check_phase2e_extras`),
  and the sole `tirith`/`GitignoreFilter` token in the whole diff is prose in
  `CLAUDE.md`.

A candidate never tested: `get_gitignore_filter(plugin_path)` at
`scripts/validate_security.py:~7549` (Issue #67) drops findings whose path the
plugin's `.gitignore` excludes. That would explain the missing
`install.sh` finding in the first test but not the second, so it is at best a
partial explanation.

### Defect B — the suite installs a real binary onto the host

`_resolve_tirith_runner` runs `brew` / `npm` / `cargo` installs (300 s timeout
each) whenever `CPV_NO_TIRITH_INSTALL` is unset. A real `tirith` is now present
at `~/.local/bin/tirith`, put there by a test run. This also explains the
absurd serial timing (571 s for 12 tests).

A test suite must not mutate the host. Every test that can reach the install
fallback needs `CPV_NO_TIRITH_INSTALL=1` — most cheaply as an autouse fixture
scoped to this module, or repo-wide in `conftest.py`, since no test should ever
want the real installer to fire.

## Acceptance criteria

- [ ] Defect A's mechanism is identified and stated as a fact, not a hypothesis
      — which branch of `check_tirith_scanner` runs in the failing case, and why.
- [ ] A fix lands whose non-vacuity is proven by mutation: reverting the fix
      makes the new/repaired test FAIL.
- [ ] ONLY once defect A's mechanism is identified (the first criterion above) AND a targeted probe
      exists that reliably FAILS against the unfixed code: the Gate 3c probe then passes 5
      consecutive times on an unchanged tree. With an unquantified intermittent failure, N green
      runs cannot distinguish "fixed" from "did not fire this time" — green runs count as evidence
      only after the mechanism is known and a probe can demonstrate the failure on demand.
- [ ] Defect B: no test run can install anything onto the host; verified by
      removing `~/.local/bin/tirith` and confirming a full suite run does not
      recreate it.
- [ ] `~/.local/bin/tirith`, installed by a prior test run, is removed (needs
      USER approval — it is outside the project tree).

## Notes

Do not "fix" defect A by widening the assertion or adding a retry. Both make
the test pass without establishing why it failed, and the whole point of Gate
3c is to catch what the serial suite cannot see.
