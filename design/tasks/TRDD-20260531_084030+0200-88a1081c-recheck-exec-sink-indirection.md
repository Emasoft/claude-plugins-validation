---
trdd-id: 88a1081c-1eb9-47c8-9eac-5dc5d04f4907
title: Recheck fixes — two literal-exec-sink suppression holes the v2.114.0 fix missed (module-container indirection + TS test-file blanket)
status: completed
created: 2026-05-31T08:40:30+0200
updated: 2026-05-31T08:40:30+0200
---

# TRDD-88a1081c — Recheck fixes: literal-exec-sink suppression holes (F1 + F2)

**Filename:** `design/tasks/TRDD-20260531_084030+0200-88a1081c-recheck-exec-sink-indirection.md`
**Tracked in:** this repo (design/tasks/ is git-tracked)
**Follows:** [[TRDD-d7d339e2-all-text-file-scanning]] (v2.114.0) — this fixes two
HIGH false-negatives an adversarial workflow recheck found in that change.

## ⏵ STATE — READ THIS FIRST ON RESUME — 2026-05-31

**Shipped in v2.114.1. status: completed.** After v2.114.0, the user ran an
adversarial multi-dimension workflow recheck (find -> verify, 6 dimensions,
12 agents). claim-verify + binary-yield came back CLEAN (all v2.114.0 claims
held), but security-bypass confirmed TWO HIGH false-negatives — both the SAME
class v2.114.0 set out to close ("a literal payload is still executed -> must
stay visible"), via sibling paths v2.114.0 did not touch:

- **F1 (Python, HIGH) — module-container indirection.** A reverse shell stored
  in a module-level data literal and EXECUTED via subscript/iteration —
  `PAYLOADS = ['bash -i >& /dev/tcp/…']; os.system(PAYLOADS[0])` — was
  suppressed to non-blocking `info` by `_match_inside_module_data_literal`
  (`_skillaudit_python_context.py`). That suppressor checks only "string lives
  in a module pure-literal container"; it never called the existing
  `_module_container_name_flows_to_sink` guard (which already detects the
  container's name reaching an exec/fs/net sink and is wired into the
  abs-path/SECRET_* branch). FIX: `_match_inside_module_data_literal` now
  returns False when the container flows to a sink — one change, fixes BOTH
  callers (the non-prose-vector `safe_literal` suppressor AND the prose-vector
  `code_fence_neutral` demoter).

- **F2 (TS/JS, HIGH) — test-file blanket suppression.** Content-threat EXECUTION
  rules (REVERSE_SHELL / CONTAINER_ESCAPE / PERSISTENCE / PRIVILEGE_ESC /
  CRED_ENV_READ / TOKEN_STEAL / TOOL_POISONING / MCP_SCHEMA_POISON / A2A_* /
  AGENT_MEMORY_MOD) were blanket-demoted to `info` in `*.test.ts` / `*.spec.ts`
  purely on filename (`_TEST_FILE_BLANKET_SUPPRESS_RULES`,
  `_skillaudit_typescript_context.py`) — and plugin tests ARE EXECUTED at
  publish time. A reverse shell parked in `evil.spec.ts` came back non-blocking.
  Python test files correctly still block these; TS was the un-fixed sibling.
  FIX: removed those 13 content-threat rules from the blanket set. Kept the
  genuine high-FP test patterns (CMD_INJECTION/SHELL_EXEC injection-surface,
  TIME_BOMB/FS_WRITE/RESOURCE_ABUSE harness noise, XSS/SQL/SSTI sanitizer-test
  DATA fixtures, localhost SSRF, env-recon, …).

**Both verified two-sided** (malicious -> BLOCKING, benign/inert -> quiet) in
`tests/test_recheck_literal_exec_sink_indirection.py` (12 tests).

**NEXT ACTION (if reopened):** none — completed.

**Load-bearing facts / gotchas:**
- F1's fix relies on `_module_container_name_flows_to_sink` handling BOTH the
  subscript form (`os.system(NAME[0])` — name appears in a sink call arg) AND
  the `for x in NAME: sink(x)` form (For-iter over the name). Both are tested.
- F2 was a JUDGEMENT call on WHICH rules to remove. The rule kept in the blanket
  set are injection-SURFACE (the call shape, not content, tripped them) or
  intentional test DATA (XSS/SQL/SSTI fed to a sanitizer-under-test). The rules
  removed fire on a SPECIFIC malicious payload that is rare in legit tests. Per
  the project invariant: over-flagging a benign test is acceptable; hiding an
  executed reverse shell is not.

## DEFERRED (NOT done) — F3 (LOW, non-security)

`validate_plugin.py:3789` RC-DATA-WRONG-ROOT-001 still gates its scan on a
`code_extensions` allowlist — a `${CLAUDE_PLUGIN_ROOT}/node_modules` reference
in a `.info`/`.rst` file is skipped. The recheck rated this LOW / non-security /
"optional, not a blocker": it is a PORTABILITY lint (report.major on a path
wiped on plugin update), not a malicious-payload or private-info scanner, and
the actual security surface (validate_security.py) is already allowlist-free.
Expanding it needs a doc-FP guard first (the regex would match the anti-pattern
quoted in documentation), so it is deferred as its own task, NOT rushed into
this security fix.

## Why the recheck caught what the implementation missed

v2.114.0 hardened the DIRECT literal-arg path (`_classify_call`) and verified it
two-sided — but the literal-exec-sink threat has THREE shapes, and only one was
covered: (a) direct arg `os.system('literal')` [fixed in .0], (b) container
indirection `os.system(NAME[i])` [F1, missed], (c) cross-language TS/JS test
files [F2, missed]. The adversarial finder explicitly probed (b) and (c) — the
shapes "any attacker would naturally use to dodge a literal-arg sink check."
Lesson: when a fix targets a *class* of bug, enumerate every code path that can
reach the same sink (direct / indirect-via-variable / cross-language), not just
the one path the originating example used.

## Acceptance (verified before publish)

- `tests/test_recheck_literal_exec_sink_indirection.py` — 12 green (two-sided).
- F1/F2 repros from the recheck now BLOCK; benign/inert controls stay quiet.
- Full suite green; mypy + ruff clean; CPV self-scan 0/0/0/0 after manifest regen.
