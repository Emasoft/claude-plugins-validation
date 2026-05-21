---
name: cpv-batch-scope-fix
description: "Apply scope-aware doctor fixes across a fleet of project folders. One cpv-doctor-agent per project in batch_scope_fix mode handles the requested scope (user / project / local / full). Auto-applies NIT and CRITICAL fixes; reports MAJOR / MINOR fixes in pending_fixes[] for user approval. LOCAL paths only. Use when applying mechanical doctor fixes across many project folders. Trigger with /cpv-batch-scope-fix."
user-invocable: true
allowed-tools: Read, Bash(python3:*,git:*,uv:*,cat:*,mkdir:*), Glob, Grep
argument-hint: "[project-folder-or-list] [--scope full|user|project|local] [--max-parallel N]"
---

# cpv-batch-scope-fix

## Overview

Counterpart to
[cpv-batch-scope-diagnose](../cpv-batch-scope-diagnose/SKILL.md).
After the doctor has diagnosed each project, this skill dispatches
one per project in **fix** mode to apply the obvious mechanical
fixes. The same scope semantics apply.

Per TRDD-a175f78d §3:

- NIT (duplicate-no-effect) → auto-applied silently.
- CRITICAL (misplaced settings.local.json entry) → auto-applied.
- MAJOR / MINOR → reported with a fix recipe in `pending_fixes[]`;
  user approves before applying.

The orchestrator body lives in this plugin's
`commands/cpv-batch-scope-fix.md` slash-command file.

## Prerequisites

- `claude-plugins-validation` plugin installed.
- LOCAL filesystem access (same constraint as
  `cpv-batch-scope-diagnose`).
- Write access to `~/.claude/` and `<project>/.claude/` —
  this skill MUTATES filesystem state in place.
- A prior diagnose pass is recommended (use
  `cpv-batch-scope-diagnose` first) so the user knows what will
  be touched.

## Inputs

See the
[cpv-batch-scope-diagnose](../cpv-batch-scope-diagnose/SKILL.md)
input table — every shape is supported identically. URL inputs
are rejected.

## Instructions

1. Confirm the user wants APPLY-FIXES (this skill mutates
   `~/.claude/` and `<project>/.claude/`).
2. Invoke the slash command body:
   ```text
   /cpv-batch-scope-fix <project-list> --scope <full|user|project|local>
   ```
3. Each per-project agent runs in `batch_scope_fix` mode and
   writes the per-project status JSON.
4. The user gets the final status table + a one-line summary +
   any `pending_fixes` for the unsafe MAJOR / MINOR recipes.
5. For pending fixes, suggest running
   `/cpv-batch-scope-diagnose-and-fix` for the same-turn variant
   OR the doctor's per-project interactive flow
   (`/cpv-doctor <one project>`).

## Output

- Unicode-bordered status table (one row per project).
- One-line DONE summary.
- Per-project scope-doctor fix reports under
  `$MAIN_ROOT/reports/scope-doctor/<ts±tz>-<project>-fix.md`.
- Per-project status JSONs carrying `before` / `after` /
  `pending_fixes`.

## Token contract

Same as `cpv-batch-scope-diagnose`.

## Error Handling

| Condition | Behaviour |
|---|---|
| URL input | CRITICAL: orchestrator surfaces the canonical "LOCAL paths only" message and stops. |
| `~/.claude/` not writable | `user` and `full` scopes report `failed` for that project. |
| Mid-fix oscillation | Per-project agent reports `partial`; other projects continue. |
| Invalid `--scope` | Resolver raises; orchestrator surfaces and stops. |

## Examples

```text
User: apply the NIT/CRITICAL fixes across my five project folders
Assistant: /cpv-batch-scope-fix /path/a /path/b /path/c /path/d /path/e --scope full

User: fix only user-scope duplicates
Assistant: /cpv-batch-scope-fix --scope user
```

## Resources

- TRDD-a175f78d — full design (cross-scope conflict severities
  in §3)
- `commands/cpv-batch-scope-fix.md` — orchestrator body (in this plugin)
- `agents/cpv-doctor-agent.md` — `batch_scope_fix` mode contract
- Sibling batch skills (this plugin): `cpv-batch-scope-diagnose`,
  `cpv-batch-scope-diagnose-and-fix`
