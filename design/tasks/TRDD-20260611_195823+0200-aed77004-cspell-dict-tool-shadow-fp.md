---
trdd-id: aed77004-2c27-4c19-8e96-6dea35fc2087
title: cspell custom-dictionary word-lists FP as TOOL_SHADOW (agent_manipulation)
column: complete
created: 2026-06-11T19:58:23+0200
updated: 2026-06-11T19:58:23+0200
current-owner: claude-cpv
task-type: bugfix
release-via: publish
test-requirements: [unit, lint, typecheck]
relevant-rules: []
implementation-commits: []
---

# cspell dictionary TOOL_SHADOW false positive

## ⏵ STATE — READ THIS FIRST ON RESUME (authoritative)

- **Done.** Carve-out implemented + two-sided tested + self-validated clean.
  Ready to publish as the next CPV patch (→ v2.126.8), which unblocks the
  `~/Code/claude-menu-system` publish (its `.cspell-words.txt` was the trigger).
- **Root cause:** `TOOL_SHADOW` (skillaudit, `category: agent_manipulation`)
  carries the bare-word pattern `monkey.?patch`. A cspell custom dictionary
  (`.cspell-words.txt`) lists the pytest-jargon words `monkeypatch` /
  `monkeypatched` / `monkeypatching` — each trips the pattern. A cspell
  word-list has no per-language context classifier, so the raw catalog runs
  and emits a MAJOR that `--strict`-blocks the publish.
- **Fix (FN-safe, mirrors issue #73 binary byte-table carve-out):** in
  `scripts/cpv_skillaudit_native.py`, `_context_classifier_verdict` now returns
  `"suppress"` for `rule_id in _BINARY_INAPPLICABLE_RULES and
  _is_cspell_dictionary(file_path)`. A cspell dictionary is vocabulary DATA —
  never loaded by Claude Code as instructions, never executed — so the
  instruction / agent-manipulation / source-shape rules in that set cannot have
  a true positive there.
- **`_is_cspell_dictionary` recogniser** is gated on a NON-instruction word-list
  extension (`.txt`/`.dict`/`.dic`/`.wordlist`/`.wl`), so a `.md`/`.json`/`.py`/
  `.sh`/`.js` instruction surface can NEVER be disguised as a dictionary. Matches
  the `cspell` filename token, a `.cspell/` directory, or the documented
  conventional names `project-words.txt` / `custom-words.txt`.
- **Two-sided proof** (real fixture, real scanner, `CPV_SCAN_CACHE=0`):
  - FP clears: TOOL_SHADOW on `.cspell-words.txt` + a file under `.cspell/` → gone.
  - Real `__proto__ =` / `Object.defineProperty` payload in a `.js` → STILL fires.
  - Same word `monkeypatch` in a NON-cspell `.txt` → STILL fires (cspell-scoped,
    not word-scoped).
  - `URL_SUSPICIOUS` on a `webhook.site/...` token hidden in the cspell file →
    STILL fires (exec/secret/exfil/decode rules are NOT in the suppressed set).

## Durable artifacts to read before acting

- `scripts/cpv_skillaudit_native.py` — `_is_cspell_dictionary` +
  `_CSPELL_DICT_EXTS` + the carve-out in `_context_classifier_verdict`.
- `tests/test_issue_cspell_dict_fp.py` — 24 two-sided regression tests.

## Why not hack the dictionary instead

Removing `monkeypatch*` from `~/Code/claude-menu-system`'s `.cspell-words.txt`
would make cspell flag every `monkeypatch` usage in its tests (the word is not in
cspell's base dictionary). The defensible fix is the CPV root cause: an
agent-manipulation rule must not fire on a non-instruction vocabulary file.

## Verification

- ruff + mypy clean on the changed file.
- 24/24 new tests pass; 132/132 sibling carve-out + classifier tests pass.
- Self-validate VALID — CRITICAL:0 MAJOR:0 MINOR:0 NIT:0 WARNING:4 (the 4
  pre-existing "agent body >2000 words" WARNINGs; baseline unchanged).
- Full serial suite: run before publish (CI parity — no-re2 + serial).
