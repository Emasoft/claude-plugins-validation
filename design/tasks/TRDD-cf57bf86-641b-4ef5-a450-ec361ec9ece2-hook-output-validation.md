# TRDD-cf57bf86-641b-4ef5-a450-ec361ec9ece2 — Hook output JSON schema validation

**TRDD ID:** `cf57bf86-641b-4ef5-a450-ec361ec9ece2`
**Filename:** `design/tasks/TRDD-cf57bf86-641b-4ef5-a450-ec361ec9ece2-hook-output-validation.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)

**Status:** Done — 2026-05-10. Initial implementation in v2.22.3 commit
54ff533 (730 LOC, 49 tests). Decision-scope hardening + reason type-check
landed on branch `wt/trdd-cf57bf86` (15 new tests; 64 tests total).
**Priority:** MAJOR (scope)
**Spec ref:** hooks.md L583–628 — per-event decision-control tables

## Context

The v2.22.2 pass-2 audit (docs_dev/audit-pass2-elements-20260417-183500.md §1.12)
identified that CPV currently validates only **hook configuration** (the `hooks.json`
shape, matcher values, allowed types, etc.) and performs **no validation of hook
output payloads**.

hooks.md now documents for every event:
- the full `hookSpecificOutput` schema shape
- the set of `permissionDecision` values accepted per event
- the precedence rules between decisions

Authors writing `type: "command"` hooks can still print any JSON to stdout and
CPV will not warn even if the emitted JSON doesn't match the spec-documented
per-event schema.

## Scope

Add a new validator module `validate_hook_output.py` that optionally takes a
sample payload (JSON string or file) plus the event name it targets, and
verifies it against the per-event decision-control table in hooks.md.

This is NOT a change to existing `validate_hook.py` — it's a new, optional
layer. Authors run it on golden-path hook outputs produced during local
testing (or capture real outputs via a dry-run fixture).

## Test scenarios

1. `PreToolUse` hook emitting `{"hookSpecificOutput": {"decision": "allow"}}` — PASS.
2. `PreToolUse` hook emitting `{"hookSpecificOutput": {"decision": "yolo"}}` — MAJOR (unknown decision value).
3. `Stop` hook emitting per-event extra fields — verify against hooks.md table.

## Dependencies

- Requires enumeration of per-event output schemas (table at hooks.md L614–628)
  into a `HOOK_OUTPUT_SCHEMAS` constant inside `cpv_validation_common.py`.
- Requires codification of the 6 permission-update types from hooks.md L1119–1126.
  (Tracked separately as pass-2 CPV-P2-m2.)

## Success criteria

- New `validate_hook_output.py` script validates a sample payload against a
  named event with CLI `--event SessionStart payload.json`.
- All 26 events have at minimum a schema that accepts the universal output
  fields from hooks.md L601–606 and denies unknown `decision` values.
- ≥10 test cases covering passing and failing per-event payloads.

## Why deferred

Adding hook-output validation requires writing per-event JSON schemas and
fixtures. It does not change the syntactic correctness of any existing plugin,
so no CRITICAL or MAJOR runtime issue blocks a v2.22.x release.

## Completion notes (2026-05-10, branch wt/trdd-cf57bf86)

Initial validator landed in v2.22.3 (commit 54ff533) — 730 LOC,
`scripts/validate_hook_output.py`, plus 49 tests. This branch hardens the
implementation with 15 additional tests + the matching code paths:

**New code paths**

- Top-level `reason` must be a string (was silently accepted as int/list).
  `null` is treated as absent (matches the `payload.get("reason")` idiom).
- Top-level `decision` is now MAJOR for events outside `TOP_LEVEL_BLOCK_EVENTS`
  (TaskCreated, TaskCompleted, SessionStart, SessionEnd, Notification, etc.).
  hooks.md L1440-1443 documents that those events only honor `continue:false`
  / exit-2; the runtime silently ignores top-level `decision`, so a hook
  emitting `decision:"block"` from TaskCreated has dead code — surfaced as MAJOR.
- PreToolUse top-level `decision` only WARNs for legacy values
  `{"approve", "block"}` (hooks.md L1010 deprecation). Any other value
  (e.g. `"yolo"`) is now MAJOR — previously the WARNING masked the bug.
- New shared constants `TOP_LEVEL_BLOCK_EVENTS` and
  `PRETOOLUSE_LEGACY_DECISIONS` for spec traceability and reuse.
- `TOP_LEVEL_BLOCK_EVENTS` includes `UserPromptExpansion` and `PostToolBatch`
  (v2.1.121 events with the same decision-control semantics as their
  parent events `UserPromptSubmit` / `PostToolUse`).

**Coverage delta**

| Was | Now |
|-----|-----|
| 49 tests | 64 tests |
| Top-level decision silently allowed for non-block events | MAJOR for 5+ events |
| `reason: 42` silently accepted | MAJOR with type info |
| PreToolUse `decision:"yolo"` only WARNed | MAJOR + WARNING |

**Files touched on branch**

- `scripts/validate_hook_output.py` — `_validate_top_level_decision` rewritten
  with 4-branch dispatch; `validate_output_payload` adds reason type-check.
- `tests/test_validate_hook_output.py` — `TestTopLevelReasonTypeCheck`,
  `TestTopLevelDecisionScope`, `TestPreToolUseLegacyDecision`,
  `TestStopReasonScopeWithPassed` (15 tests).
- `design/tasks/TRDD-cf57bf86-*.md` — Status flipped to Done with rationale.

**Out-of-scope (deferred)**

- Cross-hook precedence (TRDD-feb72fa4 / CPV-P2-M2) — held for a later wave.
- Wiring `validate_hook_output` into `validate_plugin.py` as a default check —
  per the TRDD's "optional layer" design, authors invoke this manually on
  golden-path payloads. No automatic invocation.
- Promoting `HOOK_OUTPUT_EVENT_FIELDS` into `cpv_validation_common.py` —
  the constant has a single consumer (`validate_hook_output.py` itself);
  module-local placement honours the "one source of truth" rule with no
  loss of reusability. Will move only if a second consumer appears.
