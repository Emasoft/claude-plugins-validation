---
trdd-id: TJHQD2VC
title: ENV_INJECTION misses DYLD_INSERT_LIBRARIES
column: backburner
status: tasked
created: 2026-10-23T00:14:55+0200
updated: 2026-10-23T00:19:30+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: security
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: false
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-10-23T00:14:55+0200
parent-trdd: N4GLZXZ6
---

# ENV_INJECTION misses DYLD_INSERT_LIBRARIES

## Symptom
Reported by worker F (gap 3): ENV_INJECTION misses DYLD_INSERT_LIBRARIES. The catalog pattern was not found by a literal grep, so verify the claim first, then fix.

## Code location
ENV_INJECTION rule catalog (pattern text DYLD_ followed by whitespace and equals). Related context handlers: scripts/_skillaudit_shell_context.py:499, scripts/_skillaudit_shell_context.py:1641, scripts/_skillaudit_typescript_context.py:242, scripts/_skillaudit_python_context.py:41. The catalog definition itself still has to be located (not in scripts/*.py by grep of the literal pattern).

## Proposed fix
Use the pattern \b(?:LD_PRELOAD|LD_AUDIT|LD_LIBRARY_PATH|DYLD_[A-Z_]+)\s*= and add the export, env and os.environ[...] forms.

Search words: an LD_PRELOAD or DYLD_INSERT_LIBRARIES assignment in a plugin is not flagged as environment injection.
Line numbers as of the 5.23.0 hotfix tree.
Found during the 5.23.0 release review.
Fail toward flagging; no plugin-side exception (owner policy).

## Approval log

- 2026-10-23T00:14:55+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.
- Backlog record only; not approved as a work order. Owner approval required before dev.
- 2026-10-23 — Backlog record only; not approved as a work order.
