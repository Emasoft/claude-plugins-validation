---
name: cpv-batch-full-scan-and-fix
description: "Maximum-coverage same-turn sweep across a marketplace / list / single plugin. Each cpv-plugin-fixer-agent reads every source file ONCE and runs validate + security + caching audit + caching optimize + verify-FPs + fix inline. ~5× cheaper than running the four separate batch skills sequentially. Use when applying every-checker fixes across many plugins at once. Trigger with /cpv-batch-full-scan-and-fix."
user-invocable: true
argument-hint: "<plugin-or-marketplace-or-list> [--max-parallel N]"
---

# cpv-batch-full-scan-and-fix

## Overview

Same-turn maximum-coverage sweep. Each cpv-plugin-fixer-agent subagent reads
every source file ONCE and triggers EVERY applicable in-process
checker (validate, security, caching, lint, xref, encoding, …),
classifies findings via the v2.100.x context classifier, verifies
uncertain findings via `llm-externalizer` with file-range syntax
(≤ 200 LOC per call), applies confirmed-real fixes inline, then
runs one clean-room re-check.

Same input grammar as
[cpv-batch-validate](../cpv-batch-validate/SKILL.md). The
orchestrator body lives in this plugin's
`commands/cpv-batch-full-scan-and-fix.md` slash-command file.

## Prerequisites

- `claude-plugins-validation` plugin installed.
- The five external security scanners installed (each self-skips
  when its binary is unreachable; run the `cpv-doctor` CLI as
  `cpv-doctor --install-scanners` to pre-install).
- LLM Externalizer MCP available (FP verifier).
- Write access to every plugin's tree — this skill MUTATES source
  files in place.
- For URL inputs: `git` on PATH and network access to `github.com`.

## Inputs

See the [cpv-batch-validate](../cpv-batch-validate/SKILL.md) input
table — every shape is supported identically.

## Instructions

1. Confirm the user wants the FULL sweep (validate + security +
   caching all bundled). If they only want one of those, route to
   the narrower batch skill.
2. Invoke the slash command body:
   ```text
   /cpv-batch-full-scan-and-fix <user's spec> [--max-parallel N]
   ```
3. Each per-plugin agent runs in `batch_same_turn_full` mode. The
   agent's per-plugin token cost is bounded by ONE pass over the
   source tree (vs four passes when running the separate batch
   skills sequentially).
4. The user gets the final status table + a one-line summary
   (`DONE: plugins=N clean=X fixed=Y partial=Z failed=W. Total FPs verified: F`).

## Output

- Unicode-bordered status table (one row per plugin).
- One-line DONE summary.
- Per-plugin combined re-check reports under
  `$MAIN_ROOT/reports/validate_plugin/<ts±tz>-<plugin>-full-sweep.md`.
- Per-plugin status JSONs with a `by_checker` sub-object carrying
  before/after counts per checker (validate / security / cache).
- Per-plugin commit batches in each plugin's git tree.

## Token contract

Same-turn full sweep is the cheapest way to land "everything
green" across a fleet — ~5× lower per-plugin token cost than
running the four separate batch skills sequentially.

## Error Handling

| Condition | Behaviour |
|---|---|
| Empty input | Resolver raises; orchestrator surfaces and stops. |
| Zero-plugin resolve | "Nothing to full-scan-and-fix. ✓" + stop. |
| LLM Externalizer unreachable | Agent falls back to the classifier's verdict; the per-plugin `notes` records the fallback. |
| One external scanner unreachable | That scanner self-skips for the affected plugin; other scanners still run. |
| Plugin tree not writable | Per-plugin status JSON shows `failed`. |
| One agent fails | Failed plugin gets `failed` label; others complete normally. |

## Examples

```text
User: clean-sweep every plugin in our marketplace
Assistant: /cpv-batch-full-scan-and-fix Emasoft/emasoft-plugins

User: full-scan-and-fix these three plugins in parallel
Assistant: /cpv-batch-full-scan-and-fix /path/a /path/b /path/c
```

## Resources

- TRDD-3dcbb37c §3 — full design
- `commands/cpv-batch-full-scan-and-fix.md` — orchestrator body (in this plugin)
- `agents/cpv-plugin-fixer-agent.md` — `batch_same_turn_full` mode contract
- Sibling batch skills (this plugin): `cpv-batch-validate`,
  `cpv-batch-security-audit`, `cpv-batch-caching-audit`,
  `cpv-batch-caching-optimize`, `cpv-batch-validate-and-fix`,
  `cpv-batch-fix`
