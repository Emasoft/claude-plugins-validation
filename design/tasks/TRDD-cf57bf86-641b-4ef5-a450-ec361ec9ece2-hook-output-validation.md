# TRDD-cf57bf86-641b-4ef5-a450-ec361ec9ece2 — Hook output JSON schema validation

**TRDD ID:** `cf57bf86-641b-4ef5-a450-ec361ec9ece2`
**Filename:** `design/tasks/TRDD-cf57bf86-641b-4ef5-a450-ec361ec9ece2-hook-output-validation.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)

**Status:** Not started (deferred from v2.22.2 pass-2 audit CPV-P2-M1)
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
