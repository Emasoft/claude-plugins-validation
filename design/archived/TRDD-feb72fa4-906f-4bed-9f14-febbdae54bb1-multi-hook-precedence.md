---
trdd-id: feb72fa4
title: TRDD-feb72fa4-906f-4bed-9f14-febbdae54bb1 — Cross-hook precedence analysis
column: complete
updated: 2026-08-25T17:25:39+0200
---

# TRDD-feb72fa4-906f-4bed-9f14-febbdae54bb1 — Cross-hook precedence analysis

**TRDD ID:** `feb72fa4-906f-4bed-9f14-febbdae54bb1`
**Filename:** `design/tasks/TRDD-feb72fa4-906f-4bed-9f14-febbdae54bb1-multi-hook-precedence.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)

**Status:** Done — 2026-05-10 (initial implementation shipped in v2.22.3 commit
54ff533, 442 LOC + 19 tests; v2.65.x follow-up commit on branch
`wt/trdd-feb72fa4` adds the PreToolUse event-scoping fix
(`EVENTS_WITH_PERMISSION_DECISION_PRECEDENCE`) so the precedence pass no
longer competes with the hook-output MAJOR finding for the same
authorship bug, plus 14 additional tests covering event-scoping, CLI
exit-code flow, JSON output, and regression coverage of the canonical
in-scope path.)
**Priority:** MAJOR (scope)
**Spec ref:** hooks.md L989 — decision precedence `deny > defer > ask > allow`

## Context

The v2.22.2 pass-2 audit (docs_dev/audit-pass2-elements-20260417-183500.md §1.13)
identified that CPV validates each hook configuration in isolation. When a
plugin declares **multiple** `PreToolUse` hooks that match the same tool
(e.g. two blocks with `matcher: "Bash"`), the spec at hooks.md L989 says
their decisions aggregate according to a precedence rule:

```
deny > defer > ask > allow
```

CPV does not currently aggregate across hooks — it cannot tell the author
that a `deny` hook will unconditionally override any `allow`. Authors who
expect "first match wins" or "last match wins" semantics will be silently
wrong.

## Scope

Add a `validate_hook_precedence.py` module that walks a hooks.json file,
groups hooks by `(event, matcher)`, and emits MINOR-level hints when two
or more hooks on the same pair have potentially-conflicting
`permissionDecision` values that would be resolved by precedence.

This is a **warning-only** check — CPV already validates each hook config
individually; this adds a cross-hook aggregation pass.

## Test scenarios

1. Two `PreToolUse[matcher="Bash"]` blocks — one `decision: "allow"`,
   other `decision: "deny"` → MINOR "Multiple hooks; outcome resolved by
   precedence deny > defer > ask > allow".
2. One `PreToolUse[matcher="Bash"]` and one `PreToolUse[matcher="Read"]` →
   no warning (different matchers).
3. One `PreToolUse[matcher="Bash"]` and one `PostToolUse[matcher="Bash"]` →
   no warning (different events).
4. Three `PreToolUse[matcher="Bash"]` blocks — all `allow` → no warning.

## Dependencies

- Requires inferring `permissionDecision` for command/http hooks statically.
  For `command` hooks that exec a script CPV cannot see the output, so the
  inference is best-effort (only when `permissionDecision` is declared
  inline in a `hookSpecificOutput` fixture).
- Works best combined with the hook-output validation TRDD
  (`cf57bf86-641b-4ef5-a450-ec361ec9ece2`).

## Success criteria

- `validate_hook_precedence.py` CLI reports MINOR for colliding hooks.
- No false positives for disjoint matchers/events.
- ≥6 test cases covering overlap, no-overlap, same-decision, and
  missing-decision paths.

## Why deferred

Cross-hook aggregation is a new static-analysis pass whose value depends on
hook-output validation (TRDD-cf57bf86). No existing plugin is mis-validated
by the absence of this check — CPV still catches each hook configuration
error individually. Safe to ship v2.22.2 without it.

## Implementation notes (resolved)

### v2.22.3 (commit 54ff533)
Initial precedence validator: `scripts/validate_hook_precedence.py` (442 LOC)
with `tests/test_validate_hook_precedence.py` (19 tests). Implements the
spec scenarios verbatim and exposes `validate_hook_precedence(path)` for
embedding into a future master pipeline pass.

### v2.65.x follow-up — event scoping
Initial implementation grouped hooks by `(event, matcher)` event-agnostically.
That meant a buggy `PostToolUse` hook with an inline `permissionDecision`
would surface BOTH the `validate_hook_output.py` MAJOR finding AND a MINOR
precedence finding for the same authorship bug, drowning the higher-severity
signal. The follow-up adds:

- `EVENTS_WITH_PERMISSION_DECISION_PRECEDENCE: frozenset[str] = frozenset({"PreToolUse"})`
  — spec-traceable, extensible.
- Event filter inside `detect_precedence_conflicts()` — out-of-scope events
  are skipped entirely.
- 14 new tests covering the event-scoping fix, CLI smoke (4 cases incl.
  exit codes 0/1/3 and `--json` payload), and the regression guard that
  PreToolUse remains in-scope after the filter.

Future enhancement (not in this TRDD): if the spec ever extends precedence
semantics to other events (e.g. PermissionRequest's `behavior` field), add
those events to `EVENTS_WITH_PERMISSION_DECISION_PRECEDENCE` and broaden
`extract_inline_permission_decision()` to recognize the alternate decision
key. The current implementation is event-keyed so this is a single-line
change plus a per-decision-surface adapter.

## Approval log

- 2026-08-25T17:25:39+0200 — CLOSED as complete by the CPV session (board drain; authority delegated by USER 2026-08-25). SHIPPED commits 7a9b8a8c/fb09a4b3 — validate_hook_precedence.py live (batch_ak)
