---
name: cpv-create-agent
description: Scaffold a new agent in an existing plugin (creates agents/<name>.md with valid frontmatter that passes validate_plugin out of the box).
argument-hint: <plugin-path> <agent-name> [description] [tools]
allowed-tools: Bash(uv:*)
user-invocable: true
---

# /cpv-create-agent

Add a new agent to an existing plugin. The scaffold lands at
`<plugin-path>/agents/<agent-name>.md` with valid frontmatter so the
plugin still passes `validate_plugin` immediately.

## Usage

```bash
# Minimal — name only, description prompted later
/cpv-create-agent /path/to/my-plugin code-reviewer

# Full — name + description + tools whitelist
/cpv-create-agent /path/to/my-plugin code-reviewer "Review code for quality" "Read, Bash, Grep"
```

## Under the hood

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/add_component.py" <plugin-path> \
  --type agent --name <agent-name> --description "<description>" --tools "<tools>"
```

After scaffolding:
```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/refresh_readme.py" <plugin-path>
```

## Behavior

- Existing files are NEVER overwritten (use `--force` only if you mean to).
- `agents/` directory is auto-created.
- Frontmatter follows the canonical Claude Code spec: `name`,
  `description`, `model` (defaults to `sonnet`), `tools` (set explicitly),
  `maxTurns` (sane default), and `skills` (empty initially).
- After scaffolding, the agent prompts to optionally `validate_plugin`
  the result so any drift is caught immediately.

## When to use

- Creating a domain expert (test-writer, security-auditor, doc-generator).
- Wrapping a complex workflow that needs its own context window.
- Building a multi-step pipeline (each phase as a separate agent).
