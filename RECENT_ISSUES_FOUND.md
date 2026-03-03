# Recent Issues Found — Audit of v1.7.5/v1.7.6 changes

## SHOULD-FIX (5 real bugs)

- [ ] **VP-001**: `validate_plugin.py:396` — settings.json reports "passed" even with unrecognized keys. Fix: conditional passed message.
- [ ] **VP-002**: `validate_plugin.py:684` — Shebang check reads entire file for first line. Fix: use `readline()`.
- [ ] **VH-001**: `validate_hook.py:559` — Interpreter prefix check uses `startswith` causing false negatives (e.g., `bundle.js` matches `bun`). Fix: check first token against exact match set.
- [ ] **VH-002**: `validate_hook.py:581` — Backslash detection false positives for regex/escape patterns. Fix: use Windows-style path regex.
- [ ] **VH-003**: `validate_hook.py:183` — `difflib.get_close_matches` called with `set` instead of `Sequence`. Fix: `sorted(VALID_HOOK_EVENTS)`.

## NIT (5 minor improvements)

- [ ] **VP-003**: `validate_plugin.py:331` — `.gitignore` in known_dirs is a file, not a dir. Remove it.
- [ ] **VH-004**: `validate_hook.py:574` — Bare `cd` without space not caught. Fix: add `or stripped_cmd == "cd"`.
- [ ] **VH-005**: `validate_hook.py:567` — Tilde only at command start, not mid-command. Fix: regex `(^|\s)~/`.
- [ ] **T-001**: `test_new_validation_checks.py:233,265` — Tests trigger unrelated MINOR (interpreter check). Fix: add `bash` prefix.
- [ ] **T-002**: `test_new_validation_checks.py:154` — Shebang tests trigger ruff/mypy. Fix: mock `resolve_tool_command`.
