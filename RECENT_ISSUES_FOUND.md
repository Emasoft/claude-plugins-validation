# Recent Issues Found — Audit of v1.7.5/v1.7.6 changes

## SHOULD-FIX (5 real bugs) — ALL FIXED

- [x] **VP-001**: `validate_plugin.py:396` — settings.json conditional "passed" message
- [x] **VP-002**: `validate_plugin.py:684` — Shebang check uses readline() instead of read_text()
- [x] **VH-001**: `validate_hook.py:559` — Interpreter check uses exact token match instead of startswith
- [x] **VH-002**: `validate_hook.py:581` — Backslash detection uses Windows path regex instead of broad `\\`
- [x] **VH-003**: `validate_hook.py:183` — difflib.get_close_matches uses sorted() for type safety

## NIT (5 minor improvements) — ALL FIXED

- [x] **VP-003**: `validate_plugin.py:331` — Removed `.gitignore` from known_dirs (file, not dir)
- [x] **VH-004**: `validate_hook.py:574` — Bare `cd` without space now caught
- [x] **VH-005**: `validate_hook.py:567` — Tilde check uses regex for mid-command positions
- [x] **T-001**: `test_new_validation_checks.py:233,265` — Tests use isolated commands
- [x] **T-002**: `test_new_validation_checks.py:154` — Shebang tests mock resolve_tool_command

## ADDITIONAL FIXES

- [x] **T-003**: `test_new_validation_checks.py:165,182` — Fixed `ValidationReport("test-plugin")` → `ValidationReport()`
- [x] **T-004**: `test_new_validation_checks.py:168,185` — Fixed `r.level.name` → `r.level` (string comparison)
