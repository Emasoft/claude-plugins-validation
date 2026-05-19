---
name: skills-index
description: "Catalog of every CPV skill with when-to-use guidance. Used by every CPV agent as the ONLY preloaded skill (TRDD-478d9687) — every other skill loads on demand via the Skill tool. Use when an agent needs to pick a downstream skill for the current task. Trigger with /skills-index or when reading the agent's frontmatter `skills: [skills-index]` line."
user-invocable: false
allowed-tools: Read
---

# skills-index — universal CPV skill catalog

## Overview

You are reading the universal CPV skills catalog. ALL skills are
available to any agent via the `Skill` tool — the
[skills-catalog](references/skills-catalog.md) reference holds the full
table with inputs and return contracts.

> Validation / diagnostic skills · Fix / migration skills · Scaffold / build skills · Publish / release skills · Routing / UX skills · Invocation pattern

Pick the smallest set needed for the current task. Never invoke a skill
speculatively — load on demand, drop from your turn-history when done.

## Prerequisites

- The calling agent has `Skill` in its `tools:` list (default for every CPV agent).
- A clear task statement (mode + target) so you can pick the right skill.

## Instructions

Follow these steps in order:

1. Identify the task domain (validate, fix, scaffold, publish, manage).
2. Skim the quick-lookup section below and pick a candidate skill.
3. If the candidate isn't obvious, open [skills-catalog](references/skills-catalog.md) for input shapes and return contracts.
4. Invoke the chosen skill via `Skill({skill: "claude-plugins-validation:<name>"})`.
5. Follow the loaded skill's own checklist; do NOT load another skill until the first one returns.
6. Once the downstream skill returns, surface its summary to the caller (or chain into the next skill if the routing table says so).

## Output

This catalog returns nothing itself — it lists invocations for OTHER
skills. The chosen downstream skill produces its own output. Typical
shapes:

- Validation skills → severity counts + report path.
- Fix skills → `[DONE]` / `[BLOCKED]` / `[BATCH_REQUIRED]` one-line summary.
- Scaffold skills → list of created files.

## Error Handling

- If a skill name is unknown to the harness, the `Skill` tool errors out — re-check the name in the catalog table.
- If the same skill is loaded twice in one turn, the second call is a no-op cost-wise (already cached) but wastes one tool round-trip — avoid it.
- If a skill says "Loaded by <specific-agent>" in its description, treat it as advisory only — the v2.93.0 universal-loader pattern means any agent can invoke any skill.

## Examples

```yaml
# Fix a small finding set (≤ 40 on opus / ≤ 150 on opus[1m])
Skill({skill: "claude-plugins-validation:fix-validation"})

# Cross-check marketplace against upstream
Skill({skill: "claude-plugins-validation:marketplace-authoring-contract"})

# Big-plugin handoff signal
Skill({skill: "claude-plugins-validation:batch-fix-protocol"})

# Scaffold a new skill into an existing plugin
Skill({skill: "claude-plugins-validation:scaffold-skill", args: "<plugin> <name>"})
```

## Quick lookup

Pick the smallest skill that fits the task. Full details in
[skills-catalog](references/skills-catalog.md).

| # | Domain | Skills |
|---|--------|--------|
| 1 | Validate / diagnose | `plugin-validation-skill`, `skill-validation-skill`, `cache-validation-skill`, `semantic-validation-skill` |
| 2 | Fix / migrate | `fix-validation`, `fix-marketplace-validation`, `migrate-marketplace-architecture`, `canonical-pipeline`, `batch-fix-protocol`, `deterministic-codemod`, `marketplace-authoring-contract` |
| 3 | Scaffold / build | `standardize-plugin`, `create-plugin`, `setup-plugin-repo`, `setup-github-marketplace`, `setup-marketplace-auto-notification`, `link-plugin-marketplace`, `pack-components`, `add-component-to-plugin`, `add-dependency`, `add-hook`, `register-mcp`, `scaffold-agent`, `scaffold-command`, `scaffold-skill` |
| 4 | Publish / release | `strip-dev-submodules`, `refresh-readme`, `bump-version`, `show-version`, `publish-to-marketplace` |
| 5 | Routing / UX | `plugin-management`, `cpv-main-menu-skill`, `cpv-format-menu` |

## Resources

- [skills-catalog](references/skills-catalog.md) — full per-skill table with inputs + return contracts
  > Validation / diagnostic skills · Fix / migration skills · Scaffold / build skills · Publish / release skills · Routing / UX skills · Invocation pattern
