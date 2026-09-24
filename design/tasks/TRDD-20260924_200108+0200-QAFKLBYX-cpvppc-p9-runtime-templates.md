---
trdd-id: QAFKLBYX
title: CPVPPC P9 - Runtime templates
column: backburner
status: tasked
created: 2026-09-24T20:01:08+0200
updated: 2026-09-24T20:01:08+0200
current-owner: main-agent@claude-plugins-validation
created-by: main-agent@claude-plugins-validation
task-type: feature
min-approval-requirement: none
assignee: main-agent@claude-plugins-validation
mandate: true
mandated-by: none
approved: true
approval-judge: main-agent@claude-plugins-validation
approval-datetime: 2026-09-24T20:01:08+0200
---

# CPVPPC P9 - Runtime templates

Files: templates/0.x/plugin/{launcher.sh,launcher.cmd,fetch.py,install_first_use.py,hook_runner.sh,path_shim.sh}. Key tests: missing binary fails closed; wrong-arch never chosen; fetch hash mismatch refused; quarantine cleared only after a hash match; private-repo fetch without a token fails closed; offline message; ABI change reinstalls; shim never overwrites a foreign file; a locally built binary with a bad signature refused. Done-when: tests on macOS, Linux, Windows runners. Plan: design/specs/cpvppc-plan.md section 6, row P9. Epic: TRDD-1T862D4B.

## Approval log

- 2026-09-24T20:01:08+0200 — MANDATE issued by main-agent@claude-plugins-validation (min-approval-requirement: none). Pre-approved: issuer authority >= required approver. No approval request was sent.
