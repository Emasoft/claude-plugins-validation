---
trdd-id: T7WCV3PK
title: Canonical-pipeline test-coverage audit gate — WARN-only, universal (issue #155)
column: dev
created: 2026-07-03T21:44:04+0200
updated: 2026-07-03T21:44:04+0200
current-owner: cpv-main-session
task-type: feature
release-via: publish
test-requirements: [unit]
relevant-rules: []
external-refs: ["github.com/Emasoft/claude-plugins-validation/issues/155"]
---

# TRDD-T7WCV3PK — Canonical-pipeline test-coverage audit gate (WARN-only, universal)

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative; supersedes the body) — 2026-07-03

- **What/why:** CPV issue #155 — a green CI "Test" job ≠ real coverage (a plugin can ship
  13 scripts / 1 test behind a green gate). Add a coverage-audit check that counts testable
  components vs test files and emits an **advisory WARNING** for untested components.
- **Scope decisions (LOCKED by the user 2026-07-03):**
  - **WARN-only** — NEVER fails a publish / never blocks `--strict`. (User chose "Build as WARN-only gate".)
  - **UNIVERSAL** — works for ANY plugin/skill/marketplace via GENERIC conventions; ZERO
    ai-maestro assumptions. ai-maestro-specific tightening is out of scope / opt-in only.
    (User: "CPV must work for any plugin/skill/marketplace… ai-maestro requirements are valid
    only if the plugins are from the ai-maestro-plugins marketplace.")
- **Integration point (from the investigation report — durable evidence):**
  - Add `check_test_coverage()` in `scripts/validate_plugin.py` beside `check_untested_until_release` (~L5660).
  - Register it in the `parallel_tasks` list (~L7597).
  - Emit findings via `report.warning(...)` — WARNING is structurally NON-blocking per
    `exit_code_strict()`; **zero changes** to `publish.py` or the JSON plumbing.
- **NEXT ACTION:** dispatch an opus build agent to implement `check_test_coverage()` + register it
  and write two-sided unit tests, per the plan below. Then re-validate cache-cold, docs, publish.
- **Durable artifacts to read before acting:**
  - `reports/coverage-gate-investigation/20260703_213431+0200-coverage-gate-map.md` (the file:line map,
    reuse points for component-enumeration + test-discovery, the two-sided test convention).

## Context

Issue #155 (authored by the ai-maestro MANAGER role, folds into fleet wave ai-maestro#44, but the
FEATURE itself is CPV-universal). Owner: CPV. A coverage-audit gate catches a class of quality
defect CPV currently false-negatives on (an under-tested plugin passing "green"). Building it as a
WARN improves CPV's detection without changing any publish outcome.

## Design — the coverage-audit check

- **Enumerate testable components** (reuse CPV's existing enumeration; do NOT re-derive): the
  standard plugin dirs — `scripts/` (*.py), `hooks/` (hook scripts), `skills/` (SKILL.md units),
  `commands/` (*.md), `agents/` (*.md). Generic Claude Code layout → universal.
- **Discover tests** generically: a `tests/` dir + the conventional patterns
  `test_*.py` / `*_test.py` / `*.test.{js,ts}` / `*.spec.{js,ts}`. No runner-name assumptions.
- **WARN logic:** compute testable-component count vs test-file count; emit a single advisory
  WARNING listing untested components (or the coverage ratio) when coverage is below a sane
  UNIVERSAL default. WARN-only — it is informational, never a `--strict` blocker.
- **Non-blocking proof:** emitted through `report.warning(...)`; `exit_code_strict()` already
  excludes WARNING from the blocking set (verified in the investigation). No publish.py touch.

## Plan (phases)

1. **Implement** `check_test_coverage()` in `scripts/validate_plugin.py` (~L5660) + register in
   `parallel_tasks` (~L7597). Reuse the enumeration + test-discovery helpers the report names.
2. **Tests (TDD, two-sided, FN-safe):** a fixture plugin WITH untested components → asserts the
   WARNING fires; a fixture WITH full coverage → asserts it does NOT fire; assert the finding is
   WARNING-severity and does NOT change the strict exit code. Follow the house test convention.
3. **Docs:** update help/README/CLAUDE.md inventory as needed (new check documented).
4. **Verify + ship:** cache-cold `CPV_SCAN_CACHE=0 … --strict` self-validate 0/0/0/0; regen
   self-hashes LAST; `publish.py --minor`; watch CI to green.

## Acceptance

- Untested-plugin fixture → advisory WARNING; full-coverage fixture → no WARNING (two-sided).
- The WARNING never changes a publish/`--strict` outcome (proven by test asserting exit code).
- 100% universal (generic conventions; no ai-maestro assumption); zero `publish.py`/JSON changes.
- CPV self-validate clean; CI green after publish.

## Approval log

- 2026-07-03 — User approved "Build as WARN-only gate" (AskUserQuestion). Universal-scope
  clarification recorded. Authorized as Tier-0-adjacent CPV-owned feature work under go-on-yourself.
