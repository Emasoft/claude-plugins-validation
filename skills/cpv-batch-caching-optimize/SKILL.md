---
name: cpv-batch-caching-optimize
description: "Fleet-wide parallel cache fix. Accepts local paths, GitHub URLs, marketplaces, lists, and @listfile shapes. One cpv-cache-optimizer-agent per plugin runs Phase 1 audit + Phase 2 fix + Phase 3 re-validate; Phase 4 broader refactor SKIPPED (run the cpv-cache-optimizer-agent on a single plugin to opt in). Use when applying CA-01..CA-07 fixes across many plugins. Trigger with /cpv-batch-caching-optimize."
user-invocable: true
argument-hint: "<plugin-or-marketplace-or-list> [--max-parallel N]"
---

# cpv-batch-caching-optimize

## Overview

Parallel cache-fix skill. Runs Phase 1 (Audit) → Phase 2 (Fix) →
Phase 3 (Re-validate) of the cpv-cache-optimizer-agent workflow across
every plugin in the user's input spec. Phase 4 (Broader refactor)
is **deliberately skipped** in batch mode because every Phase 4
step requires interactive per-step approval and that doesn't
compose with a parallel dispatch.

Same input grammar and dispatch shape as
[cpv-batch-validate](../cpv-batch-validate/SKILL.md). The
orchestrator body lives in this plugin's
`commands/cpv-batch-caching-optimize.md` slash-command file.

## Prerequisites

- `claude-plugins-validation` plugin installed (provides the
  `cpv-cache-optimizer-agent`, the universal input resolver, and the
  cache validator + fix recipes).
- For URL inputs: `git` on PATH and network access to `github.com`.
- Write access to every plugin's tree — this skill mutates source
  files in place (it's the fix variant of `cpv-batch-caching-audit`).
- Each plugin's tree should be a clean working tree (committed or
  stashed) before the run so the per-plugin agents can produce
  inspectable `fix(cache-CA-NN): ...` commits.

## Inputs

See the [cpv-batch-validate](../cpv-batch-validate/SKILL.md) input
table — every shape is supported identically.

## Instructions

1. Confirm the user wants to APPLY fixes (this skill mutates plugin
   files). If they want a read-only snapshot, route to
   `cpv-batch-caching-audit`.
2. Invoke the slash command body:
   ```text
   /cpv-batch-caching-optimize <user's spec> [--max-parallel N]
   ```
3. Each agent runs Phase 1 → Phase 2 → Phase 3 on its assigned
   plugin (Phase 4 skipped in batch).
4. The user gets the final status table + a one-line summary
   (`DONE: plugins=N clean=X fixed=Y partial=Z failed=W`).
5. For Phase 4 (broader refactor — extracting shared cache headers,
   restructuring agent contexts), run the `cpv-cache-optimizer-agent` on
   that single plugin interactively (it performs Phase 4 with the
   required per-step approval that does not compose with a parallel
   batch).

## Output

- Unicode-bordered status table (one row per plugin) with
  `clean` / `fixed` / `partial` / `failed` labels.
- One-line DONE summary.
- Per-plugin final cache reports under
  `$MAIN_ROOT/reports/validate_cache/<ts±tz>-<plugin>-final.md`.
- Per-plugin status JSONs under
  `<session_dir>/plugin-<index>.status.json` (carrying both
  `before` and `after` severity counts).
- Per-plugin commit batches (`fix(cache-CA-NN): ...`) in each
  plugin's git tree.

## Token contract

Same as `cpv-batch-validate` for orchestration. The per-plugin work
inside each subagent is more expensive than audit-only (the fix
phase reads + edits source) but never crosses the main-session
context.

## Error Handling

| Condition | Behaviour |
|---|---|
| Empty input | Resolver raises; orchestrator surfaces and stops. |
| Zero-plugin resolve | "Nothing to cache-optimize. ✓" + stop. |
| Plugin tree not writable | Per-plugin status JSON shows `failed` with the permission error in `notes`. |
| Fix oscillation in one plugin | The per-plugin agent stops with `partial`; the status JSON `notes` carries the oscillation diagnosis; other plugins complete normally. |
| Network failure during URL clone | Resolver raises with a remediation message; no partial batch. |

## Examples

```text
User: fix the cache-invalidation findings across this marketplace
Assistant: /cpv-batch-caching-optimize Emasoft/emasoft-plugins

User: optimize these three plugins in parallel
Assistant: /cpv-batch-caching-optimize /path/a /path/b /path/c
```

## Resources

- TRDD-3dcbb37c — full design
- `skills/cpv-cache-validation-skill/SKILL.md` — CA-01..CA-07 pattern catalog
- `commands/cpv-batch-caching-optimize.md` — orchestrator body (in this plugin)
- `commands/cpv-batch-caching-audit.md` — sibling read-only audit command (in this plugin)
- Sibling batch skills: `cpv-batch-validate`,
  `cpv-batch-security-audit`, `cpv-batch-caching-audit`,
  `cpv-batch-fix`
