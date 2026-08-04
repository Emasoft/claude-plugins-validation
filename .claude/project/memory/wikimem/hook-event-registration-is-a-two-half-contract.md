---
name: hook-event-registration-is-a-two-half-contract
description: "I added a Claude Code hook event to CPV and the suite failed on Missing output schema for event / test_hook_output_event_fields_covers_all_events / where else do I have to register a new hook event / VALID_HOOK_EVENTS and HOOK_OUTPUT_EVENT_FIELDS out of sync"
ocd: 2026-08-04
lmd: 2026-08-04
metadata:
  node_type: memory
  type: project
  tier: component
---

# hook-event-registration-is-a-two-half-contract

Registering a Claude Code hook event in CPV takes **two** edits, not one. They live in
different files and nothing at the call site couples them:

1. `scripts/cpv_validation_common.py` → `VALID_HOOK_EVENTS` — the event is a legal name.
2. `scripts/validate_hook_output.py` → `HOOK_OUTPUT_EVENT_FIELDS` — the event's allowed
   `hookSpecificOutput` keys.

`tests/test_validate_hook_output.py::TestConstants::test_hook_output_event_fields_covers_all_events`
asserts every member of the first appears in the second, and it is the ONLY thing that
couples them. Do half the change and the failure reads `Missing output schema for event
'<Name>'`.

**Why:** a name in `VALID_HOOK_EVENTS` alone means the output validator resolves
`HOOK_OUTPUT_EVENT_FIELDS.get(event, frozenset())` to an empty set by ACCIDENT rather than
by decision — indistinguishable from a documented no-output event, so a real
`hookSpecificOutput` key would be silently rejected with no row explaining why.

**How to apply:** when a spec sync adds an event, decide its output schema in the same
change and write the doc's reason in a comment beside it. An event with no decision
control gets `frozenset()` **explicitly** — that empty set is a claim, so it needs the
citation. Check the doc's decision-control table, not the sibling events: `CwdChanged` and
`FileChanged` are the same "standalone async event" family yet carry `watchPaths`, while
`DirectoryAdded` (v2.1.219) carries nothing because its add has already completed when the
hook runs.

Third-party consumers of a NEW event may also need `EVENTS_WITHOUT_MATCHERS` in
`scripts/validate_hook.py` — but only if the event genuinely takes no matcher. Read the
doc's matcher table; most recently-added events are matcher-less, and following that
pattern blindly is how a matcher-taking event gets wrongly listed there.

## Governed by

- [[claude-plugins-validation-overview]] — the project hub this component page hangs from.

## See also

- [[claude-code-hook-types-fundamentals]] — the type/event axes themselves (USER scope: the
  Claude Code hook model, independent of CPV's own constant tables).


^ATOM-A79A-MHZE [desc:"Registering a hook event needs BOTH VALID_HOOK_EVENTS and HOOK_OUTPUT_EVENT_FIELDS; one invariant test is all that couples them", keywords: missing_output_schema_for_event hook_event_registered_in_one_half VALID_HOOK_EVENTS_and_HOOK_OUTPUT_EVENT_FIELDS spec_sync_incomplete_change empty_frozenset_by_accident_not_decision, type: project, ocd: 2026-08-04, lmd: 2026-08-04]

CPV splits hook-event registration across two files with no compile-time coupling: `VALID_HOOK_EVENTS` (cpv_validation_common.py) makes the name legal, and `HOOK_OUTPUT_EVENT_FIELDS` (validate_hook_output.py) declares its allowed hookSpecificOutput keys. Only `test_hook_output_event_fields_covers_all_events` ties them together. An event added to the first alone resolves to an empty output schema by ACCIDENT (the consumer does `.get(event, frozenset())`), which is indistinguishable from a documented no-output event — so declare the schema explicitly, with the doc's reason in a comment. [^1]

## Notes and lessons learned

[^1]: [id:ATOM-HA9J-7HNH, status:valid, desc:"the v5.1.0 DirectoryAdded sync registered the event in one half only", keywords:"added_hook_event_suite_failed missing_output_schema_for_event half_a_spec_sync sibling_events_carry_fields_this_one_does_not", ocd:2026-08-04, lmd:2026-08-04] DO NOT register a new hook event by adding it to `VALID_HOOK_EVENTS` alone, BECAUSE the output-schema map is a SEPARATE file with no compile-time coupling — the consumer's `.get(event, frozenset())` then yields an empty schema by accident, and `test_hook_output_event_fields_covers_all_events` fails with `Missing output schema for event`. DO add the `HOOK_OUTPUT_EVENT_FIELDS` row in the same change, citing the doc's decision-control table — and do NOT copy a sibling's fields (`CwdChanged`/`FileChanged` carry `watchPaths`; `DirectoryAdded` correctly carries nothing).
