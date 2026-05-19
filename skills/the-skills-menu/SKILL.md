---
name: the-skills-menu
description: "Dynamic skill menu for the claude-plugins-validation plugin. Teaches agents which skills are available, when to use them, and how to load them with the Skill() tool. Use when an agent needs to pick a downstream CPV skill at runtime. Used by every CPV agent via the-skills-menu method (TRDD-478d9687)."
user-invocable: false
allowed-tools: Read
---

# the-skills-menu — universal CPV skill catalog

## Overview

This skill is the **catalog** every CPV agent consults to discover
operational skills at runtime. The agent preloads only this catalog
in its `skills:` frontmatter; everything else loads on demand via
the `Skill()` tool.

The full per-skill table (inputs, return contracts) lives in
[skills-catalog](references/skills-catalog.md).
> Validation / diagnostic skills · Fix / migration skills · Scaffold / build skills · Publish / release skills · Routing / UX skills · Invocation pattern

## Prerequisites

- The calling agent has `Skill` in its `tools:` list (default for every CPV agent).
- A clear task statement (mode + target) so you can pick the right skill.

## Instructions

Follow these steps in order:

1. Identify the task domain (validate, fix, scaffold, publish, manage).
2. Skim the Plugin Skills section below and pick a candidate.
3. If the candidate isn't obvious, open [skills-catalog](references/skills-catalog.md) for inputs + return contracts.
4. Invoke the chosen skill via `Skill({skill: "claude-plugins-validation:<name>"})` (use the plugin namespace prefix — CPV skills are always namespaced).
5. Follow the loaded skill's own checklist; do NOT load another skill until the first one returns.
6. Surface the downstream skill's summary to the caller (or chain into the next skill if the routing table says so).

## Output

This catalog returns nothing itself — it documents invocations for
OTHER skills. The chosen downstream skill produces the actual output.
Typical shapes:

- Validation skills → severity counts + report path.
- Fix skills → `[DONE]` / `[BLOCKED]` / `[BATCH_REQUIRED]` one-line summary.
- Scaffold skills → list of created files.

## Error Handling

- If a skill name is unknown to the harness, the `Skill` tool errors out — re-check the name in the catalog table.
- If the same skill is loaded twice in one turn, the second call is a no-op cost-wise (already cached) but wastes one tool round-trip — avoid it.
- If a skill description still says "Loaded by `<specific-agent>`", treat it as advisory only — the the-skills-menu method means any agent can invoke any skill.

## Examples

```yaml
# Fix a small finding set (≤ 40 on opus / ≤ 150 on opus[1m])
Skill({skill: "claude-plugins-validation:fix-validation"})

# Big-plugin handoff signal
Skill({skill: "claude-plugins-validation:batch-fix-protocol"})

# Scaffold a new skill into an existing plugin
Skill({skill: "claude-plugins-validation:scaffold-skill", args: "<plugin> <name>"})
```

## Standalone Skills

No standalone (user/local/project-scope) skills are tracked by CPV's
catalog at this time. All skills below live inside the
`claude-plugins-validation` plugin namespace.

## Plugin Skills

All entries below are invoked as
`Skill({skill: "claude-plugins-validation:<name>"})`. See
[skills-catalog](references/skills-catalog.md) for full per-skill
inputs and return contracts.

| # | Domain | Skills |
|---|--------|--------|
| 1 | Validate / diagnose | `plugin-validation-skill`, `skill-validation-skill`, `cache-validation-skill`, `semantic-validation-skill` |
| 2 | Fix / migrate | `fix-validation`, `fix-marketplace-validation`, `migrate-marketplace-architecture`, `canonical-pipeline`, `batch-fix-protocol`, `deterministic-codemod`, `marketplace-authoring-contract` |
| 3 | Scaffold / build | `standardize-plugin`, `create-plugin`, `setup-plugin-repo`, `setup-github-marketplace`, `setup-marketplace-auto-notification`, `link-plugin-marketplace`, `pack-components`, `add-component-to-plugin`, `add-dependency`, `add-hook`, `register-mcp`, `scaffold-agent`, `scaffold-command`, `scaffold-skill` |
| 4 | Publish / release | `strip-dev-submodules`, `refresh-readme`, `bump-version`, `show-version`, `publish-to-marketplace` |
| 5 | Routing / UX | `plugin-management`, `cpv-main-menu-skill`, `cpv-format-menu`, `the-skills-menu-create` |

## Resources

- [skills-catalog](references/skills-catalog.md) — full per-skill table with inputs + return contracts
  > Validation / diagnostic skills · Fix / migration skills · Scaffold / build skills · Publish / release skills · Routing / UX skills · Invocation pattern
