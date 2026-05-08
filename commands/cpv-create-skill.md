---
name: cpv-create-skill
description: Scaffold a new skill in an existing plugin (creates skills/<name>/SKILL.md with valid frontmatter that passes validate_plugin out of the box).
argument-hint: <plugin-path> <skill-name> [description]
allowed-tools: Bash(uv:*)
user-invocable: true
---

# /cpv-create-skill

Add a new skill to an existing plugin. The scaffold lands at
`<plugin-path>/skills/<skill-name>/SKILL.md` with valid frontmatter so
the plugin still passes `validate_plugin` immediately.

## Usage

```bash
# Minimal — just the path and name, description prompted later
/cpv-create-skill /path/to/my-plugin pdf-processor

# Full — name + description in one shot
/cpv-create-skill /path/to/my-plugin pdf-processor "Extract text and metadata from PDF files"
```

## Under the hood

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
- After scaffolding, the agent prompts to optionally `validate_plugin`
  the result so any drift is caught immediately.
