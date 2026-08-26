---
name: cpv-batch-validate-and-fix
description: "Same-turn parallel validate-and-fix across a marketplace / list / single plugin. Each cpv-plugin-fixer-agent reads every source file ONCE — scans + verifies false positives (via v2.100.x AST/JSON/markdown classifier + llm-externalizer with file-range syntax) + fixes inline. ~3× cheaper per plugin than running cpv-batch-validate + cpv-batch-fix separately. Use when applying validation fixes across many plugins and you trust the FP classifier chain. Trigger with /cpv-batch-validate-and-fix."
user-invocable: true
argument-hint: "<plugin-or-marketplace-or-list> [--max-parallel N]"
---

# cpv-batch-validate-and-fix

## Overview

Same-turn variant of the parallel validate + fix pipeline. Each
cpv-plugin-fixer-agent subagent reads every source file ONCE, scans +
verifies FPs inline (via the v2.100.x context classifier +
`llm-externalizer` with file-range syntax — minimum-token FP
verification), applies confirmed-real fixes, and runs one
clean-room re-check.

Same input grammar as
[cpv-batch-validate](../cpv-batch-validate/SKILL.md). The
orchestrator body lives in this plugin's
`commands/cpv-batch-validate-and-fix.md` slash-command file.

## Prerequisites

- `claude-plugins-validation` plugin installed (provides
  `scripts/print_menu.py` — the claude-menu-system bridge — and the
  `cpv-plugin-fixer-agent` agent).
- `claude-menu-system` plugin installed — the slash command emits the
  status table via its Stop-hook (through `scripts/print_menu.py`).
  Declared as a hard dependency in CPV's `plugin.json`; `print_menu.py`
  fails fast with an install hint if missing (there is no inline
  fallback renderer — TRDD-4de479a0).
- LLM Externalizer MCP available (it's the FP verifier for
  uncertain findings — without it the agent falls back to keeping
  the finding at the classifier's verdict).
- Write access to every plugin's tree — this skill MUTATES source
  files in place.
- For URL inputs: `git` on PATH and network access to `github.com`.

## Inputs

See the [cpv-batch-validate](../cpv-batch-validate/SKILL.md) input
table — every shape is supported identically.

## Instructions

1. Confirm the user wants the same-turn variant (faster, narrower
   visibility) vs the separate-pass pipeline (slower, more
   intermediate reports). When in doubt, route to
   `cpv-batch-validate` + `cpv-batch-fix` separately.
2. Invoke the slash command body:
   ```text
   /cpv-batch-validate-and-fix <user's spec> [--max-parallel N]
   ```
3. Each per-plugin agent runs in `batch_same_turn_validate_fix`
   mode. The agent reads each source file ONCE, scans + verifies
   FPs + fixes inline, then runs the final clean-room re-check.
   The command aggregates the per-plugin status JSONs into a
   CMS-shaped status-table spec, queued via `scripts/print_menu.py`;
   the claude-menu-system Stop hook emits the Unicode-bordered table
   post-turn via `systemMessage` (zero token cost — never enters the
   agent transcript). NEVER print the table inline.
4. The user gets the final status table (emitted by the Stop hook) +
   a one-line summary
   (`DONE: plugins=N clean=X fixed=Y partial=Z failed=W. Total FPs verified: F`).

## Output

- Unicode-bordered status table (one row per plugin), queued via
  `scripts/print_menu.py` and emitted post-turn by the claude-menu-system
  Stop hook through `systemMessage` (zero token cost — never enters the
  agent transcript).
- One-line DONE summary.
- Per-plugin final re-check reports under
  `$MAIN_ROOT/reports/validate_plugin/<ts±tz>-<plugin>-same-turn.md`.
- Per-plugin status JSONs under
  `<session_dir>/plugin-<index>.status.json` (carries before /
  after counts + `fps_verified` total).
- Per-plugin commit batches in each plugin's git tree.

## Token contract

Same-turn mode trades intermediate visibility for ~3× lower
per-plugin token cost — each source file is READ ONCE per plugin
instead of three times.

## Error Handling

| Condition | Behaviour |
|---|---|
| Empty input | Resolver raises; orchestrator surfaces and stops. |
| Zero-plugin resolve | "Nothing to validate-and-fix. ✓" + stop. |
| LLM Externalizer unreachable | Agent falls back to the classifier's verdict (no FP verification); the per-plugin `notes` records the fallback. |
| Plugin tree not writable | Per-plugin status JSON shows `failed`. |
| FP-verify call exceeds 200 LOC | The agent splits the suspect range into ≤ 200-LOC chunks; never reads the whole file at once. |
| One agent fails | Failed plugin gets `failed` label; others complete normally. |

## Examples

```text
User: validate-and-fix every plugin in our marketplace, fast
Assistant: /cpv-batch-validate-and-fix Emasoft/emasoft-plugins

User: same as above for these three plugins
Assistant: /cpv-batch-validate-and-fix /path/a /path/b /path/c
```

## Resources

- TRDD-3dcbb37c §3 — full design
- `commands/cpv-batch-validate-and-fix.md` — orchestrator body (in this plugin)
- `agents/cpv-plugin-fixer-agent.md` — `batch_same_turn_validate_fix` mode contract
- Sibling batch skills (this plugin): `cpv-batch-validate`,
  `cpv-batch-fix`, `cpv-batch-full-scan-and-fix`
