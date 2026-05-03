---
name: cpv-refresh-readme
description: Refresh AUTO-* marker blocks in a plugin's README (auto-detected components table)
allowed-tools: Bash(uv:*)
user-invocable: true
---

# /cpv-refresh-readme

Auto-refresh the `<!-- BEGIN AUTO-COMPONENTS -->` block in a plugin's
README.md so it never drifts out of sync with what the plugin actually
ships (agents, skills, commands, hooks, MCP servers).

## Usage

```bash
# Refresh README.md in the current directory's plugin
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/refresh_readme.py"

# Refresh a different plugin
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/refresh_readme.py" /path/to/plugin

# CI gate: exit 1 if README would change
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/refresh_readme.py" /path/to/plugin --check
```

## What it does

Detects components from the filesystem:

- `agents/*.md` → agent names
- `skills/<name>/SKILL.md` → skill names
- `commands/*.md` → command names
- `hooks/hooks.json` → presence flag
- `.mcp.json` → presence flag

…then renders a markdown table inside the `<!-- BEGIN/END AUTO-COMPONENTS -->`
markers in README.md. The marker comments are preserved; only the body
between them is rewritten — your custom prose around the block is
untouched.

If the markers are missing, the block is appended at the end of the
README on first run. You can then move it wherever you want — subsequent
runs preserve placement.

## Why marker-based?

The user can put custom text BEFORE and AFTER the auto-block — those
sections stay user-owned forever. Only the bytes between the two
markers belong to the refresh script. This is the same pattern used by
`<!--BADGES-START-->` for the version badge and (future) `<!-- BEGIN
AUTO-CATALOG -->` for marketplace plugin lists.

## Wire into publish.py

Add to your plugin's pre-push gate (or as a Gate 8.5 in publish.py):

```bash
uv run python scripts/refresh_readme.py . --check || \
  { uv run python scripts/refresh_readme.py .; \
    echo "README updated — re-stage and re-run publish"; exit 1; }
```
