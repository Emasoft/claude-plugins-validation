---
name: scaffold-agent
description: Scaffold a new agent in an existing plugin (creates agents/<name>.md with valid frontmatter that passes validate_plugin out of the box).
when_to_use: When the cpv-main-menu user picks Create → Add agent to existing plugin, or any flow needs to drop a minimal valid agents/<name>.md into a plugin
user-invocable: false
allowed-tools: Bash(uv:*)
---

# scaffold-agent

Add a new agent to an existing plugin. The scaffold lands at
`<plugin-path>/agents/<agent-name>.md` with valid frontmatter so the
plugin still passes `validate_plugin` immediately.

## Usage

Invoke via the underlying script:

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
- After scaffolding, prompt to optionally `validate_plugin` the result so
  any drift is caught immediately.

## When to use

- Creating a domain expert (test-writer, security-auditor, doc-generator).
- Wrapping a complex workflow that needs its own context window.
- Building a multi-step pipeline (each phase as a separate agent).
