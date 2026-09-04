---
trdd-id: RU0POO65
title: RC-164 regex chmod path captures prose tokens as in-plugin paths
column: todo
created: 2026-09-03T17:15:50+0200
updated: 2026-09-03T17:15:50+0200
current-owner: cpv-main-session
assignee: null
priority: 2
severity: MEDIUM
effort: M
labels: [security, false-positive, rc-164, regex-path]
task-type: bugfix
parent-trdd: null
npt: []
eht: []
blocked-by: []
relevant-rules: []
release-via: publish
test-requirements: [unit, lint, typecheck]
audit-requirements: [security-scan]
review-requirements: [code-review]
created-by: TRDD-ETDWX70R
implementation-commits: []
---

# RC-164 regex chmod path captures prose tokens as in-plugin paths

## Symptom

Running the RC-164 emitter (`validate_security.check_phase2e_extras`, security mode,
self-scan disarmed) over one machine's plugin cache on 2026-09-03 produced blocking
rows whose "in-plugin path" is a prose fragment, not a path:

- `in-plugin path '(unlike' made executable (chmod +x)` — `scripts/detectors/gh-reply-watch.py:84` (janitor)
- `in-plugin path 'for' made executable` — a skill reference `.md` (chief-of-staff)
- `in-plugin path 'it' made executable` — `tests/test_amama_stop_check.py:46`
- `` in-plugin path 'script.sh`' made executable `` — a markdown backtick span
- `in-plugin path '12' made executable` — CPV's own `CHANGELOG.md:2518` (unarmed run)

Pre-existing at HEAD (the same rows appear with the pre-fold guard); the fold rewrite
of TRDD-ETDWX70R neither introduced nor fixed them. They are invisible in the publish
gate today ONLY because of TRDD-3T170X2M (plugin mode drops RC-164 entirely).

## Root cause (to verify first — do not assume)

The regex path's `_CHMOD_EXEC_PATTERNS` capture group takes the next whitespace-delimited
token after `chmod +x` / `chmod 755` with no path-shape gate, and the fold resolves any
relative bare word against the plugin root, so an English word folds "in-tree".

## Acceptance criteria

- A captured chmod target must look like a path (contains `/`, a dotted suffix, or is
  bound to a literal path in the same fence / file) before it is folded; a bare English
  token never folds.
- Two-sided tests: the five shapes above clear; `chmod +x scripts/hook.py`,
  `chmod 755 "$CLAUDE_PLUGIN_ROOT/bin/x"`, and `os.chmod(dest, 0o755)` on a foldable
  `dest` still fire.
- Re-run the emission census (`scratchpad rc164_census.py` shape: unarmed
  `check_phase2e_extras` over every cached plugin root, newest version per plugin) and
  record before/after blocking counts on the card.
- NEVER a rule suppression or a `--strict` relaxation; the fix is a path-shape
  discriminator on the capture.

## Ordering

Must land BEFORE TRDD-3T170X2M: admitting RC-164 to the plugin gate with these
captures live would newly block third-party plugins on garbage tokens.

## Approval log
