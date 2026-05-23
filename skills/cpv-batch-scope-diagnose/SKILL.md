---
name: cpv-batch-scope-diagnose
description: "Read-only fleet-wide scope-aware doctor. One cpv-doctor-agent per project diagnoses the requested scope — user (the home .claude tree), project (the project .claude tree), local (settings.local.json), or full (all + cross-scope conflict checker). LOCAL paths only — URL inputs are CRITICAL errors because the doctor needs filesystem access to the Claude installation. Use when surveying many project folders' .claude trees at once. Trigger with /cpv-batch-scope-diagnose."
user-invocable: true
argument-hint: "[project-folder-or-list] [--scope full|user|project|local] [--max-parallel N]"
---

# cpv-batch-scope-diagnose

## Overview

Read-only scope-aware doctor across a fleet of project folders.
Each `cpv-doctor-agent` subagent diagnoses one of:

- `user`: `~/.claude/`
- `project`: `<project>/.claude/` (git-tracked entries)
- `local`: `<project>/.claude/settings.local.json`
- `full`: all of the above + cross-scope conflict checker

The orchestrator body lives in this plugin's
`commands/cpv-batch-scope-diagnose.md` slash-command file.

## Prerequisites

- `claude-plugins-validation` plugin installed.
- LOCAL filesystem access — the doctor reads `~/.claude/` and
  `<project>/.claude/`. URL inputs cannot reach these and are
  rejected with a CRITICAL message.
- A valid Claude installation at `~/.claude/` for `user` and
  `full` scopes.

## Inputs

| Shape | Example |
|---|---|
| Single project folder | the absolute path to the project root |
| List of project folders (CLI) | multiple whitespace-separated entries |
| List file | a path prefixed with `@`, e.g. an inputs list file |
| Default (no input) | `$PWD` |
| **URL or owner/repo** | **REJECTED with CRITICAL message** |

## Instructions

1. Confirm the user wants READ-ONLY diagnosis. If they want
   fixes, route to `cpv-batch-scope-fix` or
   `cpv-batch-scope-diagnose-and-fix`.
2. Invoke the slash command body:
   ```text
   /cpv-batch-scope-diagnose <project-list> --scope <full|user|project|local>
   ```
3. Each per-project agent runs in `batch_scope_diagnose` mode.
4. The user gets the final status table + a one-line summary
   (`DONE: projects=N clean=X findings=Y warning-only=Z. Conflicts total: C`).
5. If findings or conflicts surface, suggest
   `/cpv-batch-scope-fix --scope <same scope> <same target>`.

## Output

- Unicode-bordered status table (one row per project).
- One-line DONE summary.
- Per-project scope-doctor reports under
  `$MAIN_ROOT/reports/scope-doctor/<ts±tz>-<project>.md`.
- Per-project status JSONs.

## Token contract

Same as `cpv-batch-validate` — ~3-4K main-session tokens for a
17-project batch.

## Error Handling

| Condition | Behaviour |
|---|---|
| URL or owner/repo input | CRITICAL: orchestrator surfaces the canonical "LOCAL paths only" message and stops. |
| Empty input | Falls back to `$PWD`. |
| `$PWD` not a project (no `.claude-plugin/plugin.json`) | The resolver classifies it as a generic folder; if the doctor recipes can still run (e.g. user-scope only), proceed; otherwise the per-project agent reports `failed`. |
| `~/.claude/` missing (no Claude install) | `user` and `full` scopes report `failed` for that project; `project` and `local` scopes still work. |
| Invalid `--scope` value | Resolver raises `InputResolutionError`; orchestrator surfaces and stops. |

## Examples

```text
User: diagnose the .claude/ tree across my five project folders
Assistant: /cpv-batch-scope-diagnose /path/a /path/b /path/c /path/d /path/e --scope full

User: just check ~/.claude/ globally
Assistant: /cpv-batch-scope-diagnose --scope user
```

## Resources

- TRDD-a175f78d — full design
- `commands/cpv-batch-scope-diagnose.md` — orchestrator body (in this plugin)
- `agents/cpv-doctor-agent.md` — `batch_scope_diagnose` mode contract
- `scripts/cpv_scope_doctor_input.py` — local-only input resolver
- Sibling batch skills (this plugin): `cpv-batch-scope-fix`,
  `cpv-batch-scope-diagnose-and-fix`
