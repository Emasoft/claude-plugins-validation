---
trdd-id: QAFKLBYX
title: CPVPPC P9 - Runtime templates
column: backburner
status: tasked
created: 2026-09-24T20:01:08+0200
updated: 2026-09-24T20:32:35+0200
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

## Plan excerpt: section 4.2 (verbatim)

### 4.2 Canon-owned runtime behaviour
- **Launcher** `bin/<name>` + `bin/<name>.cmd`: `plugin bin/<name>-<os>-<arch>` → `${CLAUDE_PLUGIN_DATA}/bin/<sha16>/`
  (fetched on first use, verified against `bin/manifest.json`) → fail CLOSED with a clear message
  (never empty output, never exit 0); arch from the OS, aliases only from the catalog; `--version` must
  equal the plugin version.
- **Hook runner**: quoted `${CLAUDE_PLUGIN_ROOT}`; a missing script fails loudly; deps only from the lockfile.
- **First-use installer**: `${CLAUDE_PLUGIN_DATA}` only (plus declared `writes_outside_data`); lockfile;
  hash-verified; install scripts disabled; reinstall on lockfile-hash or runtime-ABI change;
  cross-process lock.

## Key tests added after card creation (2026-09-24)

From the final approved plan (design/specs/cpvppc-plan.md, row P9): node addon chosen by `process.versions.modules`, unknown ABI fails closed.
