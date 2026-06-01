---
name: cpv-batch-caching-audit
description: "Fleet-wide read-only cache audit. Accepts local paths, GitHub URLs, marketplaces, lists, and @listfile shapes. One cache-optimizer-agent per plugin runs Phase 1 only — detects CA-01..CA-07 prompt-cache invalidation patterns, no fixes. Use when surveying cache-invalidation findings across many plugins without applying changes. Trigger with /cpv-batch-caching-audit."
user-invocable: true
argument-hint: "<plugin-or-marketplace-or-list> [--max-parallel N]"
---

# cpv-batch-caching-audit

## Overview

Read-only parallel cache audit. Runs Phase 1 (Audit) of the
cache-optimizer-agent workflow across every plugin in the user's
input spec — detecting the seven documented prompt-cache invalidation
patterns (CA-01..CA-07) per plugin without applying fixes. Apply
fixes later with `/cpv-batch-caching-optimize` (which uses the same
input grammar).

Same input grammar and dispatch shape as
[cpv-batch-validate](../cpv-batch-validate/SKILL.md). The
orchestrator body lives in this plugin's
`commands/cpv-batch-caching-audit.md` slash-command file.

## Prerequisites

- `claude-plugins-validation` plugin installed (provides the
  `cache-optimizer-agent`, the universal input resolver, and the
  cache validator).
- For URL inputs: `git` on PATH and network access to `github.com`.
- Read access to every plugin's tree — cache findings include
  references to specific lines in SKILL.md, agent.md, CLAUDE.md,
  etc.

## Inputs

See the [cpv-batch-validate](../cpv-batch-validate/SKILL.md) input
table — every shape is supported identically.

## Instructions

1. Confirm the user wants AUDIT-ONLY. If they want to APPLY fixes,
   route to `cpv-batch-caching-optimize`.
2. Invoke the slash command body:
   ```text
   /cpv-batch-caching-audit <user's spec> [--max-parallel N]
   ```
3. Each agent runs Phase 1 only (no Phase 2 fix, no Phase 3
   re-validate, no Phase 4 broader refactor).
4. The user gets the final status table + a one-line summary
   (`DONE: plugins=N clean=X findings=Y warning-only=Z`).
5. If any plugin has findings, suggest
   `/cpv-batch-caching-optimize <same spec>`.

## Output

- Unicode-bordered status table (one row per plugin).
- One-line DONE summary.
- Per-plugin cache-audit reports under
  `$MAIN_ROOT/reports/validate_cache/<ts±tz>-<plugin>.md`.
- Per-plugin status JSONs under
  `<session_dir>/plugin-<index>.status.json`.

## Token contract

Same as `cpv-batch-validate`.

## Error Handling

| Condition | Behaviour |
|---|---|
| Empty input | Resolver raises; orchestrator surfaces and stops. |
| Zero-plugin resolve | "Nothing to cache-audit. ✓" + stop. |
| Plugin tree unreadable | Per-plugin status JSON shows `failed` with the I/O error in `notes`. |
| One agent fails | Failed plugin gets `failed` label; others complete normally. |
| Network failure during URL clone | Resolver raises with a remediation message; no partial batch. |

## Examples

```text
User: which plugins have CA findings in our marketplace?
Assistant: /cpv-batch-caching-audit Emasoft/emasoft-plugins

User: cache-audit these three local plugins
Assistant: /cpv-batch-caching-audit /path/a /path/b /path/c
```

## Resources

- TRDD-3dcbb37c — full design
- `skills/cache-validation-skill/SKILL.md` — CA-01..CA-07 pattern catalog
- `commands/cpv-batch-caching-audit.md` — orchestrator body (in this plugin)
- `commands/cpv-batch-caching-optimize.md` — sibling fix-mode command (in this plugin)
- Sibling batch skills: `cpv-batch-validate`,
  `cpv-batch-security-audit`, `cpv-batch-caching-optimize`,
  `cpv-batch-fix`
