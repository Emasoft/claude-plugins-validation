---
trdd-id: 10HB2U7K
title: The emitted publish.py test gate was unsatisfiable for a real suite
column: complete
created: 2026-07-28T17:13:28+0200
updated: 2026-07-28T17:13:28+0200
current-owner: cpv-session
task-type: bugfix
scope: project
release-via: publish
relevant-rules: []
implementation-commits: [155a00a7, 52d8d1a8]
external-refs: [https://github.com/Emasoft/claude-plugins-validation/issues/179]
---

# The emitted publish.py test gate was unsatisfiable for a real suite

## Problem

In the canonical `publish.py` template, the generic `run()` helper hardcoded
`subprocess.run(..., timeout=300)`, and BOTH pytest call sites inherited it. 300s
is the right bound for a lint or scan invocation; a real test suite runs for
minutes. So on any plugin whose suite exceeds five minutes, gate **G4 could never
pass** — the reporter's 13,618-test suite reached 47% at the cap and the gate
killed its own run.

A cap the suite cannot finish inside does not make the gate *stricter*; it makes
it **unprovable**. The run asserts nothing about the code while still printing
red, and a timeout is indistinguishable from a hang. Every local workaround is
worse than the bug — trimming the suite, sharding it behind the gate's back, or
marking slow tests skipped each weakens exactly what G4 exists to prove.

## Verified facts (measured before building)

- ✓ Two sites: `run()`'s inherited bound (used by `stage_tests`) and the G4
  inline `subprocess.run(..., timeout=300)` guard whose message hardcodes `300s`.
- ✓ CPV's OWN `scripts/publish.py` already uses `timeout=600` — the tree was
  fixed while the template it EMITS was not.
- ✓ No test asserted the 300s bound, so nothing pinned it as intentional.
- ✓ `standardize_plugin.py` regenerates from `generate_plugin_repo.py` live —
  but only for files that do not exist; an existing `publish.py` is never
  overwritten by a plain `--fix` (`:3785-3812`).

## Design

`run()` keeps its **300s default**, so every other call site still fails fast.
Widening it for everyone was the easy fix and the wrong one: it would slow each
hung lint step from 5 to 30 minutes for no benefit. Only the two pytest sites
pass the wider bound.

The new bound is **overridable** (`PLUGIN_TEST_SUITE_TIMEOUT`, default 1800s)
rather than a bigger constant, because a fixed bound is the defect being fixed —
300 → 1800 alone would just move the cliff for the next larger suite. A
non-positive or unparseable override falls back to the default and can never
shorten it, so a typo cannot re-create an unsatisfiable gate. The expiry message
reports the ACTUAL bound; a hardcoded `300s` starts lying the moment any caller
overrides it.

## The part that made it actually reach anyone

Fixing the generator only helps plugins scaffolded AFTER the fix. It does **not**
reach the fleet that filed #179, because standardize never overwrites an existing
`publish.py` on a plain `--fix` — and those plugins are precisely the ones that
cannot safely `--force-templates` (customized or ahead-of-canon).

So `migrate_publish_py_test_suite_timeout` mirrors the existing
`migrate_publish_py_dependency_tag`, which exists for the identical reason:
surgical, idempotent, in-place, on ANY `--fix`. Every replacement is lifted
VERBATIM out of freshly generated canon rather than re-typed — a second copy of
the canonical text is the same duplicate-source defect this migration repairs,
and it would drift the first time the generator's wording changed, leaving the
migrator installing a stale fix while reporting success.

The resolver + `run()` rewrite is a **prerequisite**, not one of several
best-effort edits: both remaining sites reference `_test_suite_timeout`, so
applying either without it would leave a `publish.py` that raises `NameError` at
push time. When that anchor does not match, the file is left byte-identical and
the shape is reported — a half-rewritten `publish.py` in someone else's repo is a
worse outcome than an unmigrated one.

## Verification

- 21 tests. Every "the suite bound is wide" assertion is paired with one proving
  the narrow default still applies elsewhere.
- The env-override tests EXECUTE the emitted resolver (compiled out of the
  generated source) rather than a copy, so they fail if the generated semantics
  drift from the intent.
- A test asserts the migrated file is **byte-identical to freshly generated
  canon**, so migrator and generator cannot silently diverge.
- The pre-fix fixture is built by inverting the shipped fix against the live
  template, with a guard test asserting the inversion really produced the broken
  shape — a fixture that silently no-opped would make every migration test pass
  vacuously.

## Notes

Also fixed the stale copy of the same shape in
`skills/cpv-setup-plugin-repo/references/plugin-hooks-and-scripts.md`, which is
what an agent READS to author the gate. Fixing only the generator would have left
the documentation prescribing the bug — the same lesson as
[[lesson-fix-what-the-tool-emits-not-just-its-own-tree]].
