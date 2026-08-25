---
trdd-id: e9a79c69
title: TRDD-e9a79c69 — Issue #24 `command` + `args` are complementary in exec form, not mutually exclusive
column: complete
updated: 2026-08-25T17:25:39+0200
---

# TRDD-e9a79c69 — Issue #24: `command` + `args` are complementary in exec form, not mutually exclusive

**TRDD ID:** `e9a79c69-01e5-443c-b845-bc33636856c9`
**Filename:** `design/tasks/TRDD-e9a79c69-01e5-443c-b845-bc33636856c9-issue-24-command-args-complementary.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)
**Status:** Done (shipped in v2.87.0)
**Date:** 2026-05-14
**Source:** https://github.com/Emasoft/claude-plugins-validation/issues/24

## Symptom

CPV's plugin validator emitted MAJOR for every hook entry that used the
documented Claude Code 2.1.139 exec form (`command` + `args` together).
The message: *"Command hook has both 'command' and 'args' — these are
mutually exclusive"*. Blocked every plugin that followed the official
CC docs canonical exec-form example. Reported against
`ai-maestro-plugin` v2.5.8.

## Root cause

In v2.83.0, I added the `args: string[]` support to `validate_hook.py`
and (incorrectly) treated `command` + `args` as mutually exclusive. The
message even acknowledged the uncertainty: *"CC docs don't define
precedence"* — but the actual docs do define the semantics clearly:

> `command` (yes) — Shell command to execute. **With `args`, the
> executable to spawn directly.** See Exec form and shell form.
>
> `args` (no) — Argument list. When present, `command` is resolved as
> an executable and spawned directly with `args` as the argument
> vector, with no shell involved.

The two fields are **complementary** in exec form. The canonical
example from the same page:

```json
{"type": "command", "command": "node",
 "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/format.js", "--fix"]}
```

## Fix (v2.87.0)

`scripts/validate_hook.py` — `validate_command_hook`:

1. **Removed** the MAJOR "mutually exclusive" check at line 2071. The
   two fields are valid together by spec.
2. **Added** the narrow case the docs DO warn about: when `command` is
   a bare name (no path separator) containing whitespace AND `args` is
   present, the spawn fails because CC tries to resolve the whole
   whitespace-containing string as an executable name. Emits MINOR
   with actionable fix instructions (move the trailing tokens into
   `args`, or remove `args` to switch to shell form).
3. **Reworked** the effective-command synthesis for downstream
   portability checks (absolute path, traversal, env-var presence):
   - shell form (command only) → command verbatim
   - exec form canonical (command + args) → space-join command + args
   - args-only legacy form → space-join args (argv[0] is the exe)
4. **Updated** the PASSED message so the form is visible in reports:
   - `Command (shell form): ...`
   - `Exec form: command=..., args=N token(s)`
   - `Args (exec form, args-only, N token(s)): ...`

## Tests

`tests/test_hook_args_field.py` rewritten:

- `test_args_and_command_both_present_emits_major` **removed** (asserted
  the bug).
- `test_canonical_exec_form_command_plus_args_passes` **added** —
  positive test for the docs' canonical exec form; asserts no
  "mutually exclusive" message resurfaces.
- `test_exec_form_bare_command_with_whitespace_emits_minor` **added** —
  the docs-warned shape (`command: "node script.js"` + `args`) emits
  the new targeted MINOR.
- `test_exec_form_command_with_path_separator_no_whitespace_warning`
  **added** — `command: "${CLAUDE_PLUGIN_ROOT}/bin/runner"` + `args`
  must NOT trigger the whitespace check.

Pre-existing tests for `args` shape (empty list, non-string element,
not-a-list, missing both, absolute path portability) retained.

## Verification

```bash
uv run pytest tests/ -n auto --dist=worksteal --maxfail=3 -q
# 5108 passed, 1 skipped, 0 failed

CPV_SKIP_GITHUB_INTEGRITY=1 uv run python scripts/validate_plugin.py . --strict
# CRITICAL: 0  MAJOR: 0  MINOR: 0  NIT: 0  WARNING: 1 (pre-existing skill size)
```

## Downstream

- `ai-maestro-plugin` v2.5.8 should publish cleanly once it uvx-installs
  CPV at v2.87.0 (or a later tag).
- Any other plugin that migrated to the 2.1.139 exec form was hit by
  the same false positive — they should also unblock automatically.

## Lessons

The v2.83.0 spec-coverage sweep introduced a rule without re-reading
the docs section it was supposed to encode. The "CC docs don't define
precedence" hedge in the error message was a signal to STOP and
re-read, not to ship the rule.

Going forward: any new validation rule that hinges on "the docs say X
about Y" must quote the docs in the test that exercises the rule, so
a future reader can spot the mismatch without leaving the test file.

## Approval log

- 2026-08-25T17:25:39+0200 — CLOSED as complete by the CPV session (board drain; authority delegated by USER 2026-08-25). SHIPPED v2.87.0 commit a7c6896b — args field + tests live (batch_ak)
