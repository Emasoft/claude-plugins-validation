# TRDD-feb72fa4-906f-4bed-9f14-febbdae54bb1 — Cross-hook precedence analysis

**TRDD ID:** `feb72fa4-906f-4bed-9f14-febbdae54bb1`
**Filename:** `design/tasks/TRDD-feb72fa4-906f-4bed-9f14-febbdae54bb1-multi-hook-precedence.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)

**Status:** Not started (deferred from v2.22.2 pass-2 audit CPV-P2-M2)
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
