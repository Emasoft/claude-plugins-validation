---
name: show-version
description: Show the CPV management tools version. Use when reporting the installed CPV CLI version. Loaded by cpv-main-menu-agent.
when_to_use: When the cpv-main-menu user picks Help → Show CPV version, or any flow needs to print the CPV management CLI version string
user-invocable: false
allowed-tools: Bash(uv:*)
---

# show-version

## Overview

Shows the CPV management CLI version by invoking `manage_plugin.py --version`. Loaded by `cpv-main-menu-agent` via the Help → Show CPV version menu branch.

## Prerequisites

- `uv` on PATH
- `${CLAUDE_PLUGIN_ROOT}` resolved (the CPV plugin must be installed)

## Instructions

1. Run `manage_plugin.py --version`.
2. Capture the printed version string.
3. Report the version to the user.

Copy this checklist and track your progress:

- [ ] `manage_plugin.py --version` invoked
- [ ] Version string captured
- [ ] Version reported to user

## Output

A single line printed to stdout, e.g. `claude-plugins-validation 2.90.0`.

## Error Handling

| Error | Resolution |
|-------|------------|
| `command not found` | Confirm the CPV plugin is installed |
| `${CLAUDE_PLUGIN_ROOT}` unset | Run from inside a Claude Code session, or set the env var manually |
| Exit non-zero | Re-run with `--debug` and check stderr |

## Examples

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/manage_plugin.py" --version
```

## Resources

- `plugin-management` skill — full management CLI documentation
- `cpv-main-menu-skill` — entry point for the Help menu branch
