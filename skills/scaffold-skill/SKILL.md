---
name: scaffold-skill
description: Scaffold a new skill in an existing plugin (creates skills/{NAME}/SKILL.md with valid frontmatter that passes validate_plugin out of the box). Use when adding a single skill to an existing plugin. Loaded by cpv-main-menu-agent.
when_to_use: When the cpv-main-menu user picks Create → Add skill to existing plugin, or any flow needs to drop a minimal valid skills/{NAME}/SKILL.md into a plugin
user-invocable: false
allowed-tools: Bash(uv:*)
---

# scaffold-skill

## Overview

Adds a new skill to an existing plugin. The scaffold lands at `<plugin-path>/skills/<skill-name>/SKILL.md` with valid frontmatter so the plugin still passes `validate_plugin` immediately. Loaded by `cpv-main-menu-agent` via the Create → Add skill menu branch.

## Prerequisites

- `uv` on PATH
- Target plugin path with `.claude-plugin/plugin.json`
- Kebab-case skill name (e.g. `my-skill`)
- A short description that includes a "Use when ..." trigger phrase

## Instructions

1. Pick a kebab-case skill name (`[a-z][a-z0-9-]*`).
2. Write a description that includes a "Use when ..." trigger phrase so agents know when to load it.
3. Run `add_component.py --type skill` with the plugin path, name, and description.
4. `skills/<name>/` directory is auto-created if missing.
5. After scaffolding, refresh the README so the new skill appears in the auto-components block.
6. Optionally validate the plugin to confirm the new skill passes.

Copy this checklist and track your progress:

- [ ] Skill name kebab-case
- [ ] Description contains "Use when ..." trigger
- [ ] `add_component.py --type skill` executed
- [ ] `skills/<name>/SKILL.md` created
- [ ] `refresh_readme.py` run
- [ ] `validate_plugin --strict` re-run

## Output

- A new file at `<plugin-path>/skills/<skill-name>/SKILL.md`.
- Frontmatter follows canonical Claude Code spec: `name`, `description`, `when_to_use`, `user-invocable`, `allowed-tools` (set to a safe minimum).
- The skill name must be kebab-case.
- Existing files are NEVER overwritten unless `--force` is passed.

## Error Handling

| Error | Resolution |
|-------|------------|
| Name not kebab-case | Use only `[a-z0-9-]`, starting with a letter |
| File exists | Re-run with `--force` only if you mean to overwrite |
| Target not a plugin | Verify `.claude-plugin/plugin.json` at the path |
| Description missing "Use when" | Edit frontmatter to add a trigger phrase |
| Skill never auto-loaded | Confirm description matches an agent's task pattern |

## Examples

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/add_component.py" /path/to/plugin \
  --type skill --name my-skill --description "What it does"
```

After scaffolding, refresh the README so the new skill appears in the `<!-- BEGIN AUTO-COMPONENTS -->` block:

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/refresh_readme.py" /path/to/plugin
```

## Resources

- `add-component-to-plugin` skill — multi-purpose scaffold wrapper
- `refresh-readme` skill — refresh the auto-components block after adding
- `plugin-validation-skill` — validate the scaffold for correctness
- `canonical-pipeline` skill — pipeline rules around skill design
