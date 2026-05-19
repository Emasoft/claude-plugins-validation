---
name: refresh-readme
description: Refresh AUTO-* marker blocks in a plugin's README (auto-detected components table). Use when the README's components list has drifted from the filesystem. Used dynamically via the-skills-menu (TRDD-478d9687).
when_to_use: When the cpv-main-menu user picks Manage → Refresh README, or any flow needs to re-render the AUTO-COMPONENTS block from current filesystem state
user-invocable: false
allowed-tools: Bash(uv:*)
---

# refresh-readme

## Overview

Auto-refreshes the `<!-- BEGIN AUTO-COMPONENTS -->` block in a plugin's `README.md` so it never drifts out of sync with what the plugin actually ships (agents, skills, commands, hooks, MCP servers). Detects components from the filesystem and renders a markdown table inside the `<!-- BEGIN/END AUTO-COMPONENTS -->` markers. Custom prose around the block stays user-owned. Loaded by `cpv-main-menu-agent` via the Manage → Refresh README menu branch.

## Prerequisites

- `uv` on PATH
- Target plugin path with `README.md` (or one will be created)
- Optional: `<!-- BEGIN AUTO-COMPONENTS -->` and `<!-- END AUTO-COMPONENTS -->` markers somewhere in the README (added on first run if missing)

## Instructions

1. Run `refresh_readme.py` against the target plugin path.
2. If markers exist, only the body between them is rewritten.
3. If markers are missing, the block is appended at the end of the README on first run. You can move it wherever you want — subsequent runs preserve placement.
4. For a CI gate, use `--check` to exit 1 if the README would change.
5. Wire into your publish.py as a pre-push gate so README never drifts in CI.

Copy this checklist and track your progress:

- [ ] `refresh_readme.py` invoked
- [ ] README updated (or `--check` confirms no drift)
- [ ] Optionally wired into publish.py Gate 8.5

## Output

- An updated `README.md` with the `<!-- BEGIN AUTO-COMPONENTS -->` block re-rendered.
- Component table includes: agents, skills, commands, presence flags for hooks/MCP.
- Custom prose before/after the block is untouched.

## Error Handling

| Error | Resolution |
|-------|------------|
| README.md missing | The script creates one on first run |
| `--check` exit 1 | README is out of date — run without `--check` to update |
| Markers in wrong place | Move them; subsequent runs preserve placement |
| Components missed | Confirm they're in canonical locations (`agents/*.md`, `skills/<name>/SKILL.md`, etc.) |

## Examples

```bash
# Refresh README.md in the current directory's plugin
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/refresh_readme.py"

# Refresh a different plugin
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/refresh_readme.py" /path/to/plugin

# CI gate: exit 1 if README would change
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/refresh_readme.py" /path/to/plugin --check
```

### What it detects

- `agents/*.md` → agent names
- `skills/<name>/SKILL.md` → skill names
- `commands/*.md` → command names
- `hooks/hooks.json` → presence flag
- `.mcp.json` → presence flag

### Why marker-based?

The user can put custom text BEFORE and AFTER the auto-block — those sections stay user-owned forever. Only the bytes between the two markers belong to the refresh script. This is the same pattern used by `<!--BADGES-START-->` for the version badge.

### Wire into publish.py

Add to your plugin's pre-push gate (or as a Gate 8.5 in publish.py):

```bash
uv run python scripts/refresh_readme.py . --check || \
  { uv run python scripts/refresh_readme.py .; \
    echo "README updated — re-stage and re-run publish"; exit 1; }
```

## Resources

- `canonical-pipeline` skill — publish.py gate patterns
- `plugin-validation-skill` — validate the refreshed README for broken references
- `add-component-to-plugin` skill — pair with refresh-readme after adding a component
