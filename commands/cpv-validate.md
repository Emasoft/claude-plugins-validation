---
name: cpv-validate
description: Interactive validation — the validator agent asks what to validate (numbered-table menu, no AskUserQuestion) and runs the right script
agent: plugin-validator
argument-hint: "[path_or_name]"
user-invocable: true
---

Validate a Claude Code plugin, skill, marketplace, or specific component.

If a path or name is provided as $ARGUMENTS, validate that target.
Otherwise, the **plugin-validator** agent prints a numbered Unicode table
(no AskUserQuestion) listing the available validators, and you reply with
the row number.

After the validation finishes and the report is on disk, the agent MUST
print the **post-validate fix prompt** — a 6-row Unicode table with rows
1-5 dispatching the **plugin-fixer** agent (or marketplace-fixer for
marketplace reports, cache-optimizer-agent for cache reports) at the
chosen `min_severity`, plus row `0 — End`. The agent NEVER asks "what's
next?" generically. The full table layout is documented in
`skills/cpv-main-menu-skill/references/menu-tree.md` §3.10.
