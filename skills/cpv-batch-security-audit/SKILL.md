---
name: cpv-batch-security-audit
description: "Fleet-wide parallel security audit. Accepts local paths, GitHub URLs, marketplaces, lists, and @listfile shapes. One cpv-plugin-validator-agent per plugin runs ONLY validate_security (5 external scanners + AI/security rules). Use when checking supply-chain risk across many plugins. Trigger with /cpv-batch-security-audit or 'security-audit every plugin in X'."
user-invocable: true
argument-hint: "<plugin-or-marketplace-or-list> [--max-parallel N]"
---

# cpv-batch-security-audit

## Overview

Parallel security-audit skill. Runs ONLY the `validate_security`
checker (5 external scanners — cc-audit, tirith, trufflehog,
semgrep, Cisco AI Defense skill-scanner — plus the in-process AI
and security rule packs) across every plugin in the user's input
spec. Skips ~36 non-security validators that the full pipeline runs,
so wall-time is lower when you only care about supply-chain risk.
(Both pipelines benefit from v2.103.0's per-file parallel scanning —
the relative gap depends on plugin shape.)

Same input grammar and dispatch shape as
[cpv-batch-validate](../cpv-batch-validate/SKILL.md). The
orchestrator body lives in this plugin's
`commands/cpv-batch-security-audit.md` slash-command file.

## Prerequisites

- `claude-plugins-validation` plugin installed (provides
  `scripts/validate_security.py`, the universal input resolver,
  `scripts/cpv_menu.py` (the claude-menu-system bridge), and the
  `cpv-plugin-validator-agent` agent).
- `claude-menu-system` plugin installed — the slash command emits the
  status table via its Stop-hook (through `scripts/cpv_menu.py`).
  Declared as a hard dependency in CPV's `plugin.json`; `cpv_menu.py`
  fails fast with an install hint if missing (there is no inline
  fallback renderer — TRDD-4de479a0).
- The five external scanners installed (cc-audit, tirith,
  trufflehog, semgrep, Cisco AI Defense skill-scanner) — each
  self-skips when its binary is unreachable, so partial coverage
  is fine; run `cpv-doctor --install-scanners` to pre-install
  everything.
- For URL inputs: `git` on PATH and network access to `github.com`.

## Inputs

See the [cpv-batch-validate](../cpv-batch-validate/SKILL.md) input
table — every shape is supported identically.

## Instructions

1. Confirm the user wants security-only (not full validation —
   that's `cpv-batch-validate`).
2. Invoke the slash command body:
   ```text
   /cpv-batch-security-audit <user's spec> [--max-parallel N]
   ```
3. The command dispatches one `cpv-plugin-validator-agent` subagent per plugin
   in `batch_security_audit` mode (each runs `validate_security`
   only) and aggregates the per-plugin status JSONs into a CMS-shaped
   status-table spec, queued via `scripts/cpv_menu.py`. The
   claude-menu-system Stop hook emits the Unicode-bordered table
   post-turn via `systemMessage` (zero token cost — never enters the
   agent transcript). NEVER print the table inline.
4. The user gets the final status table (emitted by the Stop hook) +
   a one-line summary
   (`DONE: plugins=N clean=X findings=Y warning-only=Z`).
5. If any plugin has findings, suggest `/cpv-batch-fix <same spec>`.

## Output

- Unicode-bordered status table (one row per plugin), queued via
  `scripts/cpv_menu.py` and emitted post-turn by the claude-menu-system
  Stop hook through `systemMessage` (zero token cost — never enters the
  agent transcript).
- One-line DONE summary.
- Per-plugin `validate_security` reports under
  `$MAIN_ROOT/reports/validate_security/<ts±tz>-<plugin>.md`.
- Per-plugin status JSONs under
  `<session_dir>/plugin-<index>.status.json`.

## Token contract

Same as `cpv-batch-validate` — ~3-4K main-session tokens for a
17-plugin batch.

## Error Handling

| Condition | Behaviour |
|---|---|
| Empty input | Resolver raises; orchestrator surfaces and stops. |
| Zero-plugin resolve | "Nothing to security-audit. ✓" + stop. |
| External scanner missing | The scanner self-skips for that plugin; other scanners still run. The per-plugin status JSON's `notes` field lists skipped scanners. |
| One agent fails | Failed plugin gets `failed` label in the status table; other agents complete normally. |
| Network failure during URL clone | Resolver raises with a clear remediation message; no partial batch. |

## Examples

```text
User: security-audit every plugin in our marketplace
Assistant: /cpv-batch-security-audit Emasoft/emasoft-plugins

User: I want a supply-chain snapshot across these three plugins
Assistant: /cpv-batch-security-audit /path/a /path/b /path/c
```

## Resources

- TRDD-3dcbb37c — full design
- `scripts/validate_security.py` — security checker (5 external scanners + in-process rules)
- `commands/cpv-batch-security-audit.md` — orchestrator body (in this plugin)
- Sibling batch skills: `cpv-batch-validate`,
  `cpv-batch-caching-audit`, `cpv-batch-caching-optimize`,
  `cpv-batch-fix`
