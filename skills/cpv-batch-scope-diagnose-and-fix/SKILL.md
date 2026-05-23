---
name: cpv-batch-scope-diagnose-and-fix
description: "Same-turn scope-aware diagnose + fix across a fleet of project folders. One cpv-doctor-agent per project scans + verifies + applies obvious fixes inline (NIT, CRITICAL, and safe MAJOR/MINOR auto; unsafe MAJOR/MINOR reported in pending_fixes[]). Cuts per-project token cost ~2× vs running scope-diagnose + scope-fix separately. LOCAL paths only. Use when applying obvious scope-aware doctor fixes across many project folders in one pass. Trigger with /cpv-batch-scope-diagnose-and-fix."
user-invocable: true
argument-hint: "[project-folder-or-list] [--scope full|user|project|local] [--max-parallel N]"
---

# cpv-batch-scope-diagnose-and-fix

## Overview

Same-turn variant of the scope-aware doctor batch family. Each
`cpv-doctor-agent` subagent reads each scope-anchored file ONCE,
classifies findings, and applies the obvious mechanical fixes
inline. Cuts per-project token cost ~2× vs running
`cpv-batch-scope-diagnose` + `cpv-batch-scope-fix` separately.

Per TRDD-a175f78d §3, the same-turn variant auto-applies:

- NIT (duplicate-no-effect) → silent.
- CRITICAL (misplaced local-scope entry) → silent.
- MAJOR / MINOR with a SAFE recipe → silent.
- MAJOR / MINOR with UNSAFE recipes → reported in
  `pending_fixes[]` for user approval.

The orchestrator body lives in this plugin's
`commands/cpv-batch-scope-diagnose-and-fix.md` slash-command file.

## Prerequisites

- Same as `cpv-batch-scope-fix` — LOCAL filesystem access,
  write permission on `~/.claude/` and `<project>/.claude/`.
- A valid Claude installation at `~/.claude/` for `user` /
  `full` scopes.

## Inputs

See the
[cpv-batch-scope-diagnose](../cpv-batch-scope-diagnose/SKILL.md)
input table.

## Instructions

1. Confirm the user wants the SAME-TURN variant (faster,
   narrower visibility). If they want a separate diagnose pass
   first, route to `cpv-batch-scope-diagnose` + `cpv-batch-scope-fix`.
2. Invoke the slash command body:
   ```text
   /cpv-batch-scope-diagnose-and-fix <project-list> --scope <full|user|project|local>
   ```
3. Each per-project agent runs in `batch_scope_same_turn` mode.
4. The user gets the final status table + a one-line summary +
   any `pending_fixes`.

## Output

- Unicode-bordered status table.
- One-line DONE summary.
- Per-project final scope-doctor reports under
  `$MAIN_ROOT/reports/scope-doctor/<ts±tz>-<project>-same-turn.md`.
- Per-project status JSONs.

## Token contract

Same-turn mode trades intermediate visibility for ~2× lower
per-project token cost.

## Error Handling

Same as `cpv-batch-scope-fix`.

## Examples

```text
User: clean-sweep .claude/ across all my projects
Assistant: /cpv-batch-scope-diagnose-and-fix /path/a /path/b /path/c --scope full

User: same for just my user-scope settings
Assistant: /cpv-batch-scope-diagnose-and-fix --scope user
```

## Resources

- TRDD-a175f78d — full design
- `commands/cpv-batch-scope-diagnose-and-fix.md` — orchestrator body (in this plugin)
- `agents/cpv-doctor-agent.md` — `batch_scope_same_turn` mode contract
- Sibling batch skills (this plugin): `cpv-batch-scope-diagnose`,
  `cpv-batch-scope-fix`
