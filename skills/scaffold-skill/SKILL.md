---
name: scaffold-skill
description: Scaffold a new skill in an existing plugin (creates skills/<name>/SKILL.md with valid frontmatter that passes validate_plugin out of the box).
when_to_use: When the cpv-main-menu user picks Create → Add skill to existing plugin, or any flow needs to drop a minimal valid skills/<name>/SKILL.md into a plugin
user-invocable: false
allowed-tools: Bash(uv:*)
---

# scaffold-skill

Add a new skill to an existing plugin. The scaffold lands at
`<plugin-path>/skills/<skill-name>/SKILL.md` with valid frontmatter so
the plugin still passes `validate_plugin` immediately.

## Usage

Invoke via the underlying script:

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/add_component.py" <plugin-path> \
  --type skill --name <skill-name> --description "<description>"
```

After scaffolding, refresh the README so the new skill appears in the
`<!-- BEGIN AUTO-COMPONENTS -->` block:

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/refresh_readme.py" <plugin-path>
```

## Behavior

- Existing files are NEVER overwritten (use `--force` only if you mean to).
- `skills/` directory is auto-created if missing.
- Frontmatter follows the canonical Claude Code spec: `name`,
  `description`, `when_to_use`, `user-invocable`, `allowed-tools` (set
  to a safe minimum). The skill name must be kebab-case.
- After scaffolding, prompt to optionally `validate_plugin` the result
  so any drift is caught immediately.
